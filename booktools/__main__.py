"""booktools CLI.

    python -m booktools [--project PATH] doctor
    python -m booktools [--project PATH] stats
    python -m booktools [--project PATH] pdf --target kdp|ingram|<any configured target>
    python -m booktools [--project PATH] epub
    python -m booktools [--project PATH] spine [--pages N | --pdf FILE] [--target T ...]
    python -m booktools new DEST --template DIR --vars vars.yaml
    python -m booktools [--project PATH] research ingest|query|themes|status|graph|bibliography

Exit codes: 0 ok, 1 tool/build error, 2 missing input or specs (read the message), 3 over page ceiling.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from . import __version__, deps
from .project import BookToolsError, SpecsMissing, find_project, stats_table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="booktools", description="Book production tools for the chief-editor.")
    ap.add_argument("--project", type=Path, help="book folder (default: search upward from the current folder)")
    ap.add_argument("--version", action="version", version=f"booktools {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check pandoc, WeasyPrint, PDF reader, git, epubcheck")
    sub.add_parser("stats", help="word counts vs. budgets (no pandoc needed)")

    p = sub.add_parser("pdf", help="build a print interior PDF")
    p.add_argument("--target", required=True, help="a key under production.print_targets (e.g. kdp, ingram)")
    p.add_argument("--keep-html", action="store_true", help="keep dist/_build/<target>.html for CSS debugging")
    p.add_argument("--no-snapshot", action="store_true", help="skip the Git snapshot on the 'builds' branch")
    p.add_argument("--pdf-variant", help="WeasyPrint PDF variant, e.g. pdf/x-1a, pdf/a-3b")

    e = sub.add_parser("epub", help="build the reflowable EPUB3")
    e.add_argument("--cover", type=Path, help="e-book cover image")
    e.add_argument("--no-epubcheck", action="store_true")
    e.add_argument("--snapshot", action="store_true", help="also snapshot to the 'builds' branch")

    s = sub.add_parser("spine", help="spine width, full-wrap size, SVG cover templates")
    src = s.add_mutually_exclusive_group()
    src.add_argument("--pages", type=int, help="page count (estimate) instead of reading a PDF")
    src.add_argument("--pdf", type=Path, help="interior PDF to read the page count from")
    s.add_argument("--target", action="append", help="limit to these targets (repeatable)")
    s.add_argument("--no-svg", action="store_true")

    n = sub.add_parser("new", help="create a book folder from a starter template")
    n.add_argument("dest", type=Path)
    n.add_argument("--template", type=Path, required=True, help="template folder containing template.yaml")
    n.add_argument("--vars", type=Path, required=True, help="vars.yaml with vars: and optional:")
    n.add_argument("--no-git", action="store_true")

    r = sub.add_parser("research", help="per-book local research library (GraphRAG)")
    rs = r.add_subparsers(dest="rcmd", required=True)
    ri = rs.add_parser("ingest", help="ingest research/inbox/ + research/urls.txt (or --notes)")
    ri.add_argument("--notes", action="store_true", help="ingest context/raw-material/ as notes for `themes`")
    ri.add_argument("--no-graph", action="store_true", help="skip entity-graph extraction (run `research graph` later)")
    ri.add_argument("--no-llm", action="store_true", help="embeddings only: no metadata drafting, no graph")
    rq = rs.add_parser("query", help="retrieve judge-verified, citable passages")
    rq.add_argument("question")
    rq.add_argument("-k", type=int, help="vector candidates (default research.k)")
    rq.add_argument("--chapter", help="chapter number, recorded in the citation log")
    rq.add_argument("--json", action="store_true")
    rq.add_argument("--no-judge", action="store_true", help="raw retrieval for debugging (results are not citable)")
    rt = rs.add_parser("themes", help="cluster notes into chapter themes -> context/themes.md")
    rt.add_argument("--chapters", default="8..12", help="range of chapter counts to try, e.g. 8..12")
    rt.add_argument("--no-llm", action="store_true", help="skip naming themes with the local model")
    rs.add_parser("status", help="library counts, models, unverified sources")
    rg = rs.add_parser("graph", help="extract graph for un-graphed chunks; optionally export GraphML")
    rg.add_argument("--export", type=Path, help="write the entity graph as GraphML")
    rb = rs.add_parser("bibliography", help="audit [@key] citations, write research/references.json")
    rb.add_argument("--strict", action="store_true", help="fail on unverified sources or unapproved citations")
    rb.add_argument("--all", action="store_true", help="include every verified source, not only cited ones")

    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore", message=".*SOCKS support.*")  # urllib3 noise via trafilatura
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.cmd == "doctor":
            try:
                here = find_project(args.project)
            except BookToolsError:
                here = None
            return deps.doctor(here)
        if args.cmd == "new":
            from . import new
            return new.run(args.dest, args.template, args.vars, git=not args.no_git)
        project = find_project(args.project)
        if args.cmd == "research":
            from .research import config as rconfig
            cfg = rconfig.load(project, f"research {args.rcmd}")
            if args.rcmd == "ingest":
                from .research import ingest
                return ingest.run(cfg, notes=args.notes, graph=not args.no_graph, llm=not args.no_llm)
            if args.rcmd == "query":
                from .research import retrieve
                return retrieve.run(cfg, args.question, k=args.k, chapter=args.chapter, as_json=args.json,
                                    judge=not args.no_judge)
            if args.rcmd == "themes":
                from .research import themes
                return themes.run(cfg, args.chapters, llm=not args.no_llm)
            if args.rcmd == "status":
                from .research import status
                return status.status(cfg)
            if args.rcmd == "graph":
                from .research import status
                return status.graph(cfg, args.export)
            if args.rcmd == "bibliography":
                from .research import biblio
                return biblio.run(cfg, strict=args.strict, include_all=args.all)
        if args.cmd == "stats":
            table, _ = stats_table(project, project.chapters())
            print(f"Project: {project.root}\n\n{table}")
            return 0
        if args.cmd == "pdf":
            from . import pdf
            return pdf.build(project, args.target, keep_html=args.keep_html,
                             snapshot=not args.no_snapshot, pdf_variant=args.pdf_variant)
        if args.cmd == "epub":
            from . import epub
            return epub.build(project, cover=args.cover, epubcheck=not args.no_epubcheck, snapshot=args.snapshot)
        if args.cmd == "spine":
            from . import spine
            return spine.run(project, pages=args.pages, pdf=args.pdf, targets=args.target, write_svg=not args.no_svg)
    except SpecsMissing as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except BookToolsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.code
    return 1


if __name__ == "__main__":
    sys.exit(main())
