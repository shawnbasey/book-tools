"""`booktools new`: create a book folder from a starter template.

template.yaml (in the template root, not copied):
    placeholders: {name: description, ...}        # used as {{name}} in text files
    optional: {flag: [relative paths], ...}        # removed when the flag is false
A file named `_gitignore` in the template becomes `.gitignore` in the new book.

vars.yaml:
    vars: {name: value, ...}                       # every placeholder is required
    optional: {flag: true|false, ...}              # every optional flag is required
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .project import BookToolsError

PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".txt", ".json", ".css", ".csl", ".lua", ".html", ".toml", ""}


def _force_writable(func, path, *_):
    """rmtree/unlink retry: OneDrive and Windows can mark copied files and folders read-only."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    func(path)


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, onexc=_force_writable) if sys.version_info >= (3, 12) else \
            shutil.rmtree(p, onerror=_force_writable)
    elif p.exists():
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        p.unlink()


def _load(path: Path, what: str) -> dict:
    if not path.is_file():
        raise BookToolsError(f"{what} not found: {path}", 2)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BookToolsError(f"{what} is not valid YAML: {path}\n{exc}", 2) from None
    if not isinstance(data, dict):
        raise BookToolsError(f"{what} must be a YAML mapping: {path}", 2)
    return data


def run(dest: Path, template: Path, vars_file: Path, *, git: bool = True) -> int:
    template = template.resolve()
    spec = _load(template / "template.yaml", "template.yaml")
    given = _load(vars_file, "vars file")
    placeholders = dict(spec.get("placeholders") or {})
    optional = dict(spec.get("optional") or {})
    values = {k: v for k, v in (given.get("vars") or {}).items()}
    flags = dict(given.get("optional") or {})

    problems = [f"  vars.{k:<24} {placeholders[k]}" for k in placeholders if values.get(k) in (None, "")]
    problems += [f"  optional.{k:<20} true/false: keeps or removes {optional[k]}" for k in optional if k not in flags]
    if problems:
        raise BookToolsError("AGENT ACTION REQUIRED: the vars file is incomplete. Fill these, then re-run:\n"
                             + "\n".join(problems), 2)
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise BookToolsError(f"Destination exists and is not empty: {dest}", 2)

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "book"
        shutil.copytree(template, stage, ignore=shutil.ignore_patterns("template.yaml", "__pycache__"))
        for p in [stage, *stage.rglob("*")]:  # drop read-only flags inherited from the template
            os.chmod(p, (stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC) if p.is_dir() else (stat.S_IWRITE | stat.S_IREAD))
        # Templates store `_gitignore` so the template repo's own Git doesn't apply the book's ignore rules.
        for p in list(stage.rglob("_gitignore")):
            target = p.with_name(".gitignore")
            if target.exists():
                target.unlink()
            p.rename(target)
        for flag, paths in optional.items():
            if not flags.get(flag):
                for rel in paths or []:
                    _remove(stage / rel)
        leftovers = []
        for p in stage.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = p.read_text(encoding="utf-8")
            new = PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
            if new != text:
                p.write_text(new, encoding="utf-8")
            leftovers += [f"  {p.relative_to(stage).as_posix()}: {{{{{n}}}}}" for n in PLACEHOLDER.findall(new)]
        if leftovers:
            raise BookToolsError("Unfilled placeholders (declare them in template.yaml and set them in vars):\n"
                                 + "\n".join(sorted(set(leftovers))), 2)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.rmdir()
        shutil.copytree(stage, dest)

    print(f"Created {dest}")
    if git and shutil.which("git"):
        title = values.get("title", dest.name)
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", f"chore: scaffold {title} from book starter"],
                    ["git", "tag", "-a", "v0.1-scaffold", "-m", "Scaffold"]):
            res = subprocess.run(cmd, cwd=dest, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"git step failed ({' '.join(cmd[:2])}): {res.stderr.strip()}")
                break
        else:
            print("Git: main branch, initial commit, tag v0.1-scaffold")
    print("Next: write the framework-specific files, then open this folder as its own workspace.")
    return 0
