"""Reflowable EPUB3 via pandoc, with kindle.css and an auto-generated TOC."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import deps
from .citeproc import citeproc_args
from .pdf import STYLES_DIR
from .project import BookToolsError, Project, combine, stats_table
from .snapshot import git_snapshot

PRINT_ONLY_RE = re.compile(r"^# [^\n]*\{[^}\n]*\.print-only[^}\n]*\}[^\n]*\n.*?(?=^# |\Z)", re.M | re.S)


def strip_print_only(markdown: str) -> str:
    """Drop H1 sections tagged {.print-only} (e.g. the print title page; pandoc makes the EPUB one)."""
    return PRINT_ONLY_RE.sub("", markdown)


def metadata_yaml(project: Project) -> str:
    b = project.book
    if not b.get("title") or not b.get("author"):
        raise BookToolsError(f"book.title and book.author are required in {project.config_path}", 2)
    ns = b.get("site") or f"urn:book:{project.slug}"
    fields = {
        "title": b.get("title"), "subtitle": b.get("subtitle"), "author": b.get("author"),
        "lang": b.get("language") or "en-US", "rights": b.get("rights"), "publisher": b.get("publisher"),
        "date": dt.date.today().isoformat(),
        "identifier": b.get("isbn_ebook") or "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ns}/{project.slug}")),
    }
    # JSON-encoded strings are valid YAML scalars (safe quoting).
    return "---\n" + "".join(f"{k}: {json.dumps(str(v))}\n" for k, v in fields.items() if v) + "...\n"


def resolve_css(project: Project) -> Path:
    name = project.get("production.epub.css") or "kindle.css"
    for candidate in (project.root / name, STYLES_DIR / name):
        if candidate.is_file():
            return candidate
    raise BookToolsError(f"EPUB stylesheet not found: {name}", 2)


def build(project: Project, *, cover: Path | None = None, epubcheck: bool = True, snapshot: bool = False) -> int:
    exe, version = deps.pandoc()
    css = resolve_css(project)
    chapters = project.chapters()
    if cover is None:
        configured = project.get("production.epub.cover")
        candidates = [project.root / configured] if configured else [
            project.cover_dir / f"ebook-cover.{ext}" for ext in ("jpg", "jpeg", "png")]
        cover = next((c for c in candidates if c.is_file()), None)
    elif not cover.is_file():
        raise BookToolsError(f"Cover image not found: {cover}", 2)

    project.dist_dir.mkdir(parents=True, exist_ok=True)
    out = project.dist_dir / f"{project.slug}.epub"
    split = ["--split-level=1"] if version >= (3, 0) else ["--epub-chapter-level=1"]

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        md = workdir / "book.md"
        md.write_text(strip_print_only(combine(chapters)), encoding="utf-8")
        meta = workdir / "metadata.yaml"
        meta.write_text(metadata_yaml(project), encoding="utf-8")
        cmd = [exe, str(md), "-f", "markdown+smart", "-t", "epub3", "-o", str(out),
               "--metadata-file", str(meta), "--css", str(css), "--toc", "--toc-depth=2",
               "--resource-path", f"{project.manuscript_dir}{os.pathsep}{project.root}",
               *split, *deps.no_highlight_args(version), *citeproc_args(project)]
        if cover:
            cmd += ["--epub-cover-image", str(cover)]
        print(f"Building EPUB3 from {len(chapters)} files (pandoc {'.'.join(map(str, version))}, css {css.name})...")
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise BookToolsError(f"pandoc failed:\n{res.stderr}")
        if res.stderr.strip():
            print(f"pandoc warnings:\n{res.stderr.rstrip()}")

    table, words = stats_table(project, chapters)
    print("\n" + table)
    size_kb = out.stat().st_size / 1024
    print(f"\nOutput:     {out}  ({size_kb:,.0f} KB)")
    print(f"Cover:      {cover.name if cover else 'none (add assets/cover/ebook-cover.jpg)'}")

    checker = shutil.which("epubcheck")
    if checker and epubcheck:
        chk = subprocess.run([checker, str(out)], capture_output=True, text=True)
        print(f"epubcheck:  {'PASS' if chk.returncode == 0 else 'FAIL'}")
        if chk.returncode != 0:
            print(chk.stdout[-3000:] + chk.stderr[-2000:])
    elif not checker:
        print("epubcheck:  not installed (optional)")

    if snapshot:
        git_snapshot(project.root, "epub", {
            "target": "epub", "words": words, "epub": out.name,
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "built": dt.datetime.now().isoformat(timespec="seconds"),
        })
    print(f"\nbook-index.yaml -> build: last_epub: {dt.date.today().isoformat()}, last_epub_size_kb: {size_kb:.0f}")
    return 0
