"""Minimal client for a local OpenAI-compatible server (LM Studio), with optional model
load/unload through the `lms` CLI and an optional single-GPU lock adapter."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ..project import BookToolsError

SMALL_HINTS = ("embed", "bge-", "nomic", "whisper")


class LocalAI:
    def __init__(self, endpoint: str, gpu_lock: Path | None = None, own_models: tuple[str, ...] = ()):
        self.endpoint = endpoint.rstrip("/")
        self.host = re.sub(r"/v1$", "", self.endpoint)
        self.own = set(own_models)
        self.lock_mod = _load_lock(gpu_lock) if gpu_lock else None
        self.lms = shutil.which("lms")

    # ---- HTTP ---------------------------------------------------------------
    def _req(self, path: str, body: dict | None = None, timeout: float = 600) -> Any:
        url = (self.endpoint if path.startswith("/") and not path.startswith("/api") else self.host) + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            raise BookToolsError(f"Local AI server error {exc.code} on {path}: {exc.read()[:400]!r}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise BookToolsError(
                f"Cannot reach the local AI server at {self.endpoint} ({exc}).\n"
                "  Start LM Studio's server (e.g. `lms server start --port <port>`) and check research.endpoint.") from exc

    def available(self) -> list[str]:
        return [m["id"] for m in self._req("/models", timeout=10).get("data", [])]

    def loaded(self) -> list[str]:
        try:
            data = self._req("/api/v0/models", timeout=10)
        except BookToolsError:
            return []
        return [m["id"] for m in data.get("data", []) if m.get("state") == "loaded"]

    # ---- model lifecycle ----------------------------------------------------
    @contextmanager
    def session(self, model: str, context_length: int = 8192) -> Iterator["LocalAI"]:
        """Load `model` if needed (respecting the GPU lock), unload it afterwards if we loaded it."""
        claim = None
        if self.lock_mod and not any(h in model.lower() for h in SMALL_HINTS):
            busy = [h for h in self.lock_mod.holders()
                    if not self.lock_mod.is_small(h) and h.split(":", 1)[-1] not in self.own]
            if busy:
                raise BookToolsError(f"GPU busy with {busy}. Free it first (single-GPU rule), then re-run.")
            try:
                claim = self.lock_mod.Claim(f"lmstudio:{model}").__enter__()
            except SystemExit as exc:
                raise BookToolsError(str(exc)) from None
        ours = False
        try:
            if self.lms and model not in self.loaded():
                res = subprocess.run([self.lms, "load", model, "-y", "--context-length", str(context_length)],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode != 0:
                    raise BookToolsError(f"`lms load {model}` failed:\n{res.stdout[-800:]}{res.stderr[-800:]}")
                ours = True
            yield self
        finally:
            if ours:
                subprocess.run([self.lms, "unload", model], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if claim:
                claim.__exit__(None, None, None)

    # ---- calls --------------------------------------------------------------
    def embed(self, model: str, texts: list[str], batch: int = 32) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            data = self._req("/embeddings", {"model": model, "input": texts[i:i + batch]})
            out += [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        v = np.asarray(out, dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True).clip(min=1e-12)
        return v

    def chat(self, model: str, prompt: str, max_tokens: int = 800, system: str | None = None) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens,
                "reasoning_effort": "none"}
        data = self._req("/chat/completions", body)
        text = data["choices"][0]["message"].get("content") or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def chat_json(self, model: str, prompt: str, max_tokens: int = 800, retries: int = 1) -> Any:
        for attempt in range(retries + 1):
            text = self.chat(model, prompt + ("" if attempt == 0 else "\n\nReply with valid JSON only."), max_tokens)
            parsed = extract_json(text)
            if parsed is not None:
                return parsed
        return None


def extract_json(text: str) -> Any:
    """First JSON object or array in `text` (tolerates code fences and chatter)."""
    text = re.sub(r"```(?:json)?", "", text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                c = text[i]
                if in_str:
                    esc = (c == "\\") and not esc
                    if c == '"' and not esc:
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                elif c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    return None


def _load_lock(path: Path):
    spec = importlib.util.spec_from_file_location("booktools_gpu_lock", path)
    if not spec or not spec.loader:
        raise BookToolsError(f"Cannot load GPU lock module {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("holders", "is_small", "Claim"):
        if not hasattr(mod, name):
            raise BookToolsError(f"GPU lock module {path} lacks `{name}` (needs holders(), is_small(), Claim).")
    return mod
