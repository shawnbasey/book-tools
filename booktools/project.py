"""Project discovery, config access, and manuscript reading.

A *project* is any folder containing `context/book-index.yaml` and `manuscript/NN-*.md`.
Nothing book-specific is hard-coded here: titles, trim, margins, and printer specs
all come from the project's book-index.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_REL = Path("context") / "book-index.yaml"
FILE_RE = re.compile(r"^(\d{2})-[\w-]+\.md$")
FRONTMATTER_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
DEFAULT_TOLERANCE = 0.10


class BookToolsError(Exception):
    """A user-fixable problem. Message is printed as-is; exit code attached."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


class SpecsMissing(BookToolsError):
    """Required production specs are absent; message is an instruction block for the agent."""


@dataclass
class Chapter:
    path: Path
    number: int
    meta: dict[str, Any]
    body: str
    words: int

    @property
    def budget(self) -> int:
        try:
            return int(self.meta.get("word_budget") or 0)
        except (TypeError, ValueError):
            return 0


@dataclass
class Project:
    root: Path
    config: dict[str, Any] = field(default_factory=dict)

    # ---- locations -------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_REL

    @property
    def manuscript_dir(self) -> Path:
        return self.root / "manuscript"

    @property
    def dist_dir(self) -> Path:
        return self.root / "dist"

    @property
    def cover_dir(self) -> Path:
        return self.root / "assets" / "cover"

    # ---- config sections ---------------------------------------------------
    @property
    def book(self) -> dict[str, Any]:
        return self.config.get("book") or {}

    @property
    def production(self) -> dict[str, Any]:
        return self.config.get("production") or {}

    @property
    def slug(self) -> str:
        return str(self.book.get("slug") or self.root.name)

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a dotted path like 'production.trim.width_in' from the config."""
        node: Any = self.config
        for part in dotted.split("."):
            if not isinstance(node, dict) or node.get(part) is None:
                return default
            node = node[part]
        return node

    def print_targets(self) -> dict[str, dict[str, Any]]:
        return self.production.get("print_targets") or {}

    # ---- manuscript ----------------------------------------------------------
    def chapters(self) -> list[Chapter]:
        if not self.manuscript_dir.is_dir():
            raise BookToolsError(f"Manuscript folder not found: {self.manuscript_dir}", 2)
        out: list[Chapter] = []
        for p in self.manuscript_dir.iterdir():
            m = FILE_RE.match(p.name)
            if p.is_file() and m:
                meta, body = split_frontmatter(p.read_text(encoding="utf-8"))
                out.append(Chapter(p, int(m.group(1)), meta, body, count_words(body)))
        if not out:
            raise BookToolsError(f"No NN-*.md files in {self.manuscript_dir}", 2)
        out.sort(key=lambda c: (c.number, c.path.name))
        nums = [c.number for c in out]
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        if dupes:
            raise BookToolsError(f"Duplicate chapter numbers in manuscript/: {dupes}", 2)
        return out


def find_project(start: Path | None = None) -> Project:
    """Walk up from `start` (default: cwd) to the folder holding context/book-index.yaml."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        cfg = candidate / CONFIG_REL
        if cfg.is_file():
            try:
                data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise BookToolsError(f"Invalid YAML in {cfg}:\n{exc}", 2) from exc
            return Project(candidate, data)
    raise BookToolsError(
        f"No book project found at or above {here}.\n"
        f"A project needs {CONFIG_REL.as_posix()} (see book-tools/templates/book-index.template.yaml).\n"
        "Run from inside the book folder or pass --project PATH.", 2)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.lstrip("﻿")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def count_words(markdown_body: str) -> int:
    """Approximate printed word count: strips comments, attributes, and Markdown syntax."""
    body = re.sub(r"<!--.*?-->", " ", markdown_body, flags=re.S)
    body = re.sub(r"\{[.#][^}]*\}", " ", body)
    body = re.sub(r"^:::.*$", " ", body, flags=re.M)
    body = re.sub(r"^```.*$", " ", body, flags=re.M)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = re.sub(r"\]\([^)]*\)", "]", body)
    return len(WORD_RE.findall(body))


def combine(chapters: list[Chapter]) -> str:
    return "\n\n".join(c.body.strip() + "\n" for c in chapters)


def stats_table(project: Project, chapters: list[Chapter]) -> tuple[str, int]:
    """Return (printable table, total words)."""
    tol = float(project.get("production.budget_tolerance", DEFAULT_TOLERANCE))
    target = project.get("production.word_target")
    lines = [f"{'File':<42}{'Status':<9}{'Words':>7}{'Budget':>8}  Check", "-" * 76]
    for c in chapters:
        status = str(c.meta.get("status", "?"))
        check = "-"
        if status == "stub":
            check = "stub"
        elif c.budget and c.words:
            ratio = c.words / c.budget
            check = (f"OVER +{(ratio - 1) * 100:.0f}%" if ratio > 1 + tol
                     else f"under {(ratio - 1) * 100:.0f}%" if ratio < 1 - tol else "ok")
        lines.append(f"{c.path.name:<42}{status:<9}{c.words:>7,}{c.budget:>8,}  {check}")
    total = sum(c.words for c in chapters)
    budget = sum(c.budget for c in chapters)
    lines += ["-" * 76,
              f"{'TOTAL':<51}{total:>7,}{budget:>8,}  " + (f"(book target {int(target):,})" if target else "")]
    if target and total > int(target) * (1 + tol):
        lines.append(f"WARNING: manuscript is over the {int(target):,}-word target by more than {tol:.0%}.")
    return "\n".join(lines), total
