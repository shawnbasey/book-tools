"""booktools CLI.

    python -m booktools [--project PATH] doctor
    python -m booktools [--project PATH] stats
    python -m booktools [--project PATH] pdf --target kdp|ingram|<any configured target>
    python -m booktools [--project PATH] epub
    python -m booktools [--project PATH] spine [--pages N | --pdf FILE] [--target T ...]

Exit codes: 0 ok, 1 tool/build error, 2 missing input or specs (read the message), 3 over page ceiling.
"""
from __future__ import annotations

import argparse
import sys
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

    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.cmd == "doctor":
            return deps.doctor()
        project = find_project(args.project)
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
