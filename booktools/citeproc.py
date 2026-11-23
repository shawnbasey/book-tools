"""Pandoc arguments for citations (light module: no research dependencies needed for builds)."""
from __future__ import annotations

import re
from pathlib import Path

from .project import BookToolsError, Project

STYLES_DIR = Path(__file__).resolve().parent / "styles"
FOOTNOTE_FILTER = STYLES_DIR / "footnotes.lua"
CITE_RE = re.compile(r"\[[^\[\]]*@[A-Za-z0-9_][^\[\]]*\]")


def uses_citations(project: Project) -> bool:
    return any(CITE_RE.search(c.body) for c in project.chapters())


def resolve_csl(project: Project) -> Path:
    style = str((project.config.get("research") or {}).get("citation_style") or "chicago-notes-bibliography")
    for candidate in (project.root / style, project.root / f"{style}.csl", STYLES_DIR / "csl" / f"{style}.csl"):
        if candidate.is_file():
            return candidate
    raise BookToolsError(f"Citation style '{style}' not found (bundled: chicago-notes-bibliography). "
                         "Put a .csl file in the project and set research.citation_style to its path.", 2)


def citeproc_args(project: Project) -> list[str]:
    """[] when the manuscript has no citations; otherwise pandoc citeproc args (refs must exist)."""
    if not uses_citations(project):
        return []
    refs = project.root / "research" / "references.json"
    if not refs.is_file():
        raise BookToolsError(
            "The manuscript contains [@key] citations but research/references.json is missing.\n"
            "  Run:  python -m booktools research bibliography", 2)
    return ["--citeproc", "--bibliography", str(refs), "--csl", str(resolve_csl(project)),
            "--metadata", "suppress-bibliography=false"]
