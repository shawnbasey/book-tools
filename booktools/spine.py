"""Spine width + full-wrap cover dimensions and SVG wrap templates, for any trim/printer.

Width  = bleed + back(trim W) + spine(pages x per-page factor) + front(trim W) + bleed
Height = bleed + trim H + bleed

Every measurement comes from the project's `production:` config. Missing values stop
the run with an AGENT ACTION REQUIRED block (see specs.py).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from . import deps, specs
from .project import BookToolsError, Project

PT = 72.0  # SVG user units per inch

SPINE_NEEDS = ["trim.width_in", "trim.height_in",
               "target.bleed_in", "target.spine_in_per_page", "target.safe_in",
               "target.spine_safe_in", "target.barcode_in", "target.min_pages"]


@dataclass
class Wrap:
    target: str
    label: str
    pages: int
    trim_w: float
    trim_h: float
    bleed: float
    factor: float
    safe: float
    spine_safe: float
    barcode: tuple[float, float]
    spine_text_min_pages: int | None
    spine_text_min_in: float | None

    @property
    def spine(self) -> float:
        return self.pages * self.factor

    @property
    def width(self) -> float:
        return 2 * self.bleed + 2 * self.trim_w + self.spine

    @property
    def height(self) -> float:
        return 2 * self.bleed + self.trim_h

    @property
    def spine_text_ok(self) -> bool:
        if self.spine_text_min_pages is not None and self.pages < self.spine_text_min_pages:
            return False
        if self.spine_text_min_in is not None and self.spine < self.spine_text_min_in:
            return False
        return self.spine > 2 * self.spine_safe


def _wrap(project: Project, target: str, pages: int) -> Wrap:
    t = project.get(f"production.print_targets.{target}")
    bc = t["barcode_in"]
    if not (isinstance(bc, (list, tuple)) and len(bc) == 2):
        raise BookToolsError(f"production.print_targets.{target}.barcode_in must be [width, height].", 2)
    opt = lambda k, cast: cast(t[k]) if t.get(k) is not None else None  # noqa: E731
    return Wrap(
        target=target, label=str(t.get("label") or target), pages=pages,
        trim_w=specs.num(project.get("production.trim.width_in")),
        trim_h=specs.num(project.get("production.trim.height_in")),
        bleed=specs.num(t["bleed_in"]), factor=specs.num(t["spine_in_per_page"]),
        safe=specs.num(t["safe_in"]), spine_safe=specs.num(t["spine_safe_in"]),
        barcode=(specs.num(bc[0]), specs.num(bc[1])),
        spine_text_min_pages=opt("spine_text_min_pages", int),
        spine_text_min_in=opt("spine_text_min_in", float),
    )


def _fmt(inches: float) -> str:
    return f'{inches:.4f}"  {inches * 25.4:8.2f} mm  {round(inches * 300):>5} px@300dpi'


def svg_template(w: Wrap, title: str) -> str:
    u = lambda inches: round(inches * PT, 3)  # noqa: E731
    W, H = w.width, w.height
    back_x, spine_x = w.bleed, w.bleed + w.trim_w
    front_x = spine_x + w.spine
    top = w.bleed
    bc_w, bc_h = w.barcode
    bc_x, bc_y = spine_x - w.safe - bc_w, top + w.trim_h - w.safe - bc_h
    mid = H / 2
    lab = 'font-family="Arial, sans-serif" fill="#555555"'
    safe_rect = lambda x: (f'    <rect x="{u(x + w.safe)}" y="{u(top + w.safe)}" width="{u(w.trim_w - 2 * w.safe)}" '  # noqa: E731
                           f'height="{u(w.trim_h - 2 * w.safe)}" stroke="#0066ff" stroke-dasharray="6 4"/>')
    p = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.4f}in" height="{H:.4f}in" viewBox="0 0 {u(W)} {u(H)}">',
        f'  <title>{title} — {w.label} full-wrap cover template ({w.pages} pp)</title>',
        f'  <desc>Trim {w.trim_w}x{w.trim_h} in, bleed {w.bleed} in, spine {w.spine:.4f} in '
        f'({w.pages} pages x {w.factor}). Generated {dt.date.today().isoformat()} by booktools spine. '
        'Hide or delete the "guides" layer before export.</desc>',
        f'  <g id="artwork">\n    <rect x="0" y="0" width="{u(W)}" height="{u(H)}" fill="#ffffff"/>\n  </g>',
        '  <g id="guides" fill="none" stroke-width="0.75">',
        f'    <rect x="0" y="0" width="{u(W)}" height="{u(H)}" stroke="#ff0000"/>',
        f'    <rect x="{u(w.bleed)}" y="{u(top)}" width="{u(W - 2 * w.bleed)}" height="{u(w.trim_h)}" stroke="#000000"/>',
        f'    <line x1="{u(spine_x)}" y1="0" x2="{u(spine_x)}" y2="{u(H)}" stroke="#000000"/>',
        f'    <line x1="{u(front_x)}" y1="0" x2="{u(front_x)}" y2="{u(H)}" stroke="#000000"/>',
        safe_rect(back_x), safe_rect(front_x),
    ]
    if w.spine > 2 * w.spine_safe:
        p.append(f'    <rect x="{u(spine_x + w.spine_safe)}" y="{u(top + w.safe)}" width="{u(w.spine - 2 * w.spine_safe)}" '
                 f'height="{u(w.trim_h - 2 * w.safe)}" stroke="#0066ff" stroke-dasharray="3 3"/>')
    fs = min(10.0, max(4.0, u(w.spine) * 0.45))
    cx = u(spine_x + w.spine / 2)
    p += [
        f'    <rect x="{u(bc_x)}" y="{u(bc_y)}" width="{u(bc_w)}" height="{u(bc_h)}" stroke="#ff8800" fill="#ffeedd" fill-opacity="0.6"/>',
        f'    <text x="{u(bc_x + bc_w / 2)}" y="{u(bc_y + bc_h / 2)}" font-size="9" text-anchor="middle" {lab}>BARCODE {bc_w}" x {bc_h}"</text>',
        f'    <text x="{u(back_x + w.trim_w / 2)}" y="{u(mid)}" font-size="20" text-anchor="middle" {lab}>BACK COVER</text>',
        f'    <text x="{u(front_x + w.trim_w / 2)}" y="{u(mid)}" font-size="20" text-anchor="middle" {lab}>FRONT COVER</text>',
        f'    <text x="{cx}" y="{u(mid)}" font-size="{fs:.1f}" text-anchor="middle" dominant-baseline="middle" '
        f'transform="rotate(90 {cx} {u(mid)})" {lab}>SPINE {w.spine:.4f}" {"(text OK)" if w.spine_text_ok else "(no text)"}</text>',
        f'    <text x="{u(w.bleed + 0.05)}" y="{u(H - 0.03)}" font-size="6" {lab}>{w.label} · {w.pages} pp · '
        f'wrap {W:.4f}" x {H:.4f}" · red=bleed black=trim/fold blue=safe orange=barcode</text>',
        '  </g>', '</svg>',
    ]
    return "\n".join(p) + "\n"


def run(project: Project, *, pages: int | None = None, pdf: Path | None = None,
        targets: list[str] | None = None, write_svg: bool = True) -> int:
    targets = targets or list(project.print_targets())
    specs.require(project, SPINE_NEEDS, "spine", targets)
    for warning in specs.staleness_warnings(project, targets):
        print(warning)

    if pages is None:
        candidates = [pdf] if pdf else [project.dist_dir / f"{project.slug}-{t}.pdf" for t in targets]
        found = next((c for c in candidates if c and c.is_file()), None)
        if found is None:
            raise BookToolsError(
                "AGENT ACTION REQUIRED: no page count available.\n"
                "  Build the interior first:  python -m booktools pdf --target <target>\n"
                "  Or, for an early estimate, pass the expected count:  python -m booktools spine --pages N\n"
                "  (Never finalize a cover from an estimate; rerun after the final interior build.)", 2)
        pages, source = deps.pdf_page_count(found), found.name
    else:
        source = "--pages"

    title = str(project.book.get("title") or project.slug)
    print(f"Page count (from {source}): {pages}")
    results: list[Wrap] = []
    for t in targets:
        tp = pages + (pages % 2) if project.get(f"production.print_targets.{t}.even_pages", True) else pages
        w = _wrap(project, t, tp)
        results.append(w)
        min_pages = int(project.get(f"production.print_targets.{t}.min_pages"))
        print(f"\n=== {w.label} ({w.factor}\"/page{', rounded to even ' + str(tp) if tp != pages else ''}) ===")
        if tp < min_pages:
            print(f"BELOW MINIMUM: {tp} pages < {min_pages} for {w.label}.")
        print(f"Trim:          {w.trim_w}\" x {w.trim_h}\"   Bleed: {w.bleed}\"")
        print(f"Spine width:   {_fmt(w.spine)}")
        print(f"Full wrap W:   {_fmt(w.width)}   = {w.bleed} + {w.trim_w} + {w.spine:.4f} + {w.trim_w} + {w.bleed}")
        print(f"Full wrap H:   {_fmt(w.height)}   = {w.bleed} + {w.trim_h} + {w.bleed}")
        print(f"Spine text:    {'yes' if w.spine_text_ok else 'NO: leave spine blank or use a small logo'}")

    if write_svg:
        project.cover_dir.mkdir(parents=True, exist_ok=True)
        print()
        for w in results:
            out = project.cover_dir / f"cover-wrap-{w.target}.svg"
            out.write_text(svg_template(w, title), encoding="utf-8")
            print(f"SVG template:  {out}")

    print("\nbook-index.yaml -> cover:")
    print(f"  last_calculated: {dt.date.today().isoformat()}")
    for w in results:
        print(f"  {w.target}: {{pages: {w.pages}, spine_in: {w.spine:.4f}, wrap_in: [{w.width:.4f}, {w.height:.4f}]}}")
    print("\nCross-check against each printer's official cover template/calculator before upload.")
    return 0
