"""Print-on-demand interior PDF: manuscript -> pandoc HTML5 -> WeasyPrint PDF.

Page geometry (trim + margins) is generated from the project's config and layered
over the base stylesheet, so the same CSS works for any trim size.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from pathlib import Path

from . import deps, specs
from .project import BookToolsError, Project, combine, stats_table
from .snapshot import git_snapshot

STYLES_DIR = Path(__file__).resolve().parent / "styles"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="$lang$">
<head>
<meta charset="utf-8">
<title>$pagetitle$</title>
</head>
<body>
$if(toc)$
<nav id="TOC" role="doc-toc">
<h1 class="toc-title">Contents</h1>
$table-of-contents$
</nav>
$endif$
$body$
</body>
</html>
"""

PDF_NEEDS = ["trim.width_in", "trim.height_in",
             "target.margins_in.top", "target.margins_in.bottom",
             "target.margins_in.inside", "target.margins_in.outside"]


def resolve_css(project: Project, target: str) -> Path:
    name = project.get(f"production.print_targets.{target}.css") or f"pod_{target}.css"
    for candidate in (project.root / name, STYLES_DIR / name):
        if candidate.is_file():
            return candidate
    fallback = STYLES_DIR / "pod_ingram.css"
    print(f"No stylesheet '{name}' in project or library; using strict-black {fallback.name}.")
    return fallback


def geometry_css(project: Project, target: str) -> str:
    w = specs.num(project.get("production.trim.width_in"))
    h = specs.num(project.get("production.trim.height_in"))
    m = project.get(f"production.print_targets.{target}.margins_in")
    top, bottom, inside, outside = (specs.num(m[k]) for k in ("top", "bottom", "inside", "outside"))
    return (f"@page {{ size: {w}in {h}in; margin: {top}in {outside}in {bottom}in {inside}in; }}\n"
            f"@page :left {{ margin-left: {outside}in; margin-right: {inside}in; }}\n"
            f"@page :right {{ margin-left: {inside}in; margin-right: {outside}in; }}\n")


def _render_html(project: Project, version: tuple[int, ...], exe: str, workdir: Path) -> str:
    import os
    import subprocess

    chapters = project.chapters()
    front = [c for c in chapters if c.number == 0]
    body = [c for c in chapters if c.number != 0]
    template = workdir / "print-template.html"
    template.write_text(HTML_TEMPLATE, encoding="utf-8")
    common = ["-f", "markdown+smart", "-t", "html5", "--section-divs",
              "--resource-path", f"{project.manuscript_dir}{os.pathsep}{project.root}",
              *deps.no_highlight_args(version)]

    def run(md_text: str, name: str, extra: list[str]) -> subprocess.CompletedProcess:
        src = workdir / name
        src.write_text(md_text, encoding="utf-8")
        res = subprocess.run([exe, str(src), *common, *extra], capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise BookToolsError(f"pandoc failed on {name}:\n{res.stderr}")
        if res.stderr.strip():
            print(f"pandoc warnings ({name}):\n{res.stderr.rstrip()}")
        return res

    front_html = run(combine(front), "front.md", []).stdout if front else ""
    lang = str(project.book.get("language") or "en")
    main = run(combine(body), "body.md",
               ["--standalone", f"--template={template}", "--toc", "--toc-depth=1",
                "--metadata", f"pagetitle={project.book.get('title') or project.slug}",
                "--metadata", f"lang={lang}"]).stdout
    return main.replace("<body>", "<body>\n" + front_html, 1)


def build(project: Project, target: str, *, keep_html: bool = False, snapshot: bool = True,
          pdf_variant: str | None = None) -> int:
    specs.require(project, PDF_NEEDS, f"pdf --target {target}", [target])
    for w in specs.staleness_warnings(project, [target]):
        print(w)

    exe, version = deps.pandoc()
    wp = deps.weasyprint()
    css = resolve_css(project, target)
    chapters = project.chapters()

    project.dist_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = project.dist_dir / f"{project.slug}-{target}.pdf"
    print(f"Building {target} interior from {len(chapters)} files "
          f"(pandoc {'.'.join(map(str, version))}, weasyprint {wp.__version__}, css {css.name})...")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        html = _render_html(project, version, exe, workdir)
        if keep_html:
            keep = project.dist_dir / "_build" / f"{target}.html"
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_text(html, encoding="utf-8")
            print(f"HTML kept:  {keep}")
        html_path = workdir / "book.html"
        html_path.write_text(html, encoding="utf-8")
        options = {"pdf_variant": pdf_variant} if pdf_variant else {}
        wp.HTML(filename=str(html_path), base_url=str(project.manuscript_dir)).write_pdf(
            str(out_pdf),
            stylesheets=[wp.CSS(filename=str(css)), wp.CSS(string=geometry_css(project, target))],
            **options)

    pages = deps.pdf_page_count(out_pdf)
    table, words = stats_table(project, chapters)
    print("\n" + table)
    ceiling = project.get("production.page_ceiling")
    min_pages = project.get(f"production.print_targets.{target}.min_pages")
    print(f"\nOutput:     {out_pdf}  ({out_pdf.stat().st_size / 1024:,.0f} KB)")
    print(f"Pages:      {pages}" + (f"  (ceiling {ceiling})" if ceiling else "")
          + ("  odd count: printer pads to even" if pages % 2 else ""))
    print(f"Words:      {words:,}")

    if snapshot:
        git_snapshot(project.root, target, {
            "target": target, "pages": pages, "words": words, "pdf": out_pdf.name,
            "sha256": hashlib.sha256(out_pdf.read_bytes()).hexdigest(),
            "built": dt.datetime.now().isoformat(timespec="seconds"),
            "files": {c.path.name: c.words for c in chapters},
        })

    print(f"\nbook-index.yaml -> build: last_target: {target}, last_page_count: {pages}, "
          f"last_word_count: {words}, last_build: {dt.date.today().isoformat()}")
    if min_pages and pages < int(min_pages):
        print(f"\nBELOW MINIMUM: {pages} pages < {min_pages} required by {target}.")
    if ceiling and pages > int(ceiling):
        print(f"\nOVER CEILING: {pages} pages > {ceiling}. Trim chapters flagged OVER above.")
        return 3
    return 0
