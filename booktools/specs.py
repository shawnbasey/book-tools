"""Production-spec validation.

Printer requirements change and differ per trim, paper, ink, and page count, so the
library never assumes them. When a value is missing, it raises SpecsMissing with an
instruction block telling the agent exactly what to look up and where to write it.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .project import Project, SpecsMissing

STALE_AFTER_DAYS = 180

# Hints only. Shown to the agent as "last known — verify". Never used as values.
FIELD_HINTS: dict[str, str] = {
    "trim.width_in": "Trim width in inches (common: 5, 5.25, 5.5, 6, 7, 8.5).",
    "trim.height_in": "Trim height in inches (common: 8, 8.25, 8.5, 9, 10, 11).",
    "interior.ink": "bw | standard-color | premium-color (KDP) / bw | color (Ingram).",
    "interior.paper": "white | cream (B&W) — color interiors are white only.",
    "margins_in.top": "Top margin, inches.",
    "margins_in.bottom": "Bottom margin, inches.",
    "margins_in.inside": "Inside (gutter) margin, inches. Printer minimum grows with page count "
                         "(KDP last known: 24-150pp 0.375, 151-300pp 0.5, 301-500pp 0.625, "
                         "501-700pp 0.75, 701+pp 0.875). Add comfort space above the minimum.",
    "margins_in.outside": "Outside margin, inches (KDP last known minimum 0.25 no-bleed, 0.375 with bleed).",
    "bleed_in": "Cover bleed per outer edge, inches (KDP and Ingram last known: 0.125).",
    "spine_in_per_page": "Spine thickness per page, inches, for THIS paper + ink "
                         "(KDP last known: B&W white 0.002252, B&W cream 0.0025, standard color 0.002252, "
                         "premium color 0.002347. Ingram: varies by paper stock — read it off their "
                         "Cover Template Generator for your page count and divide by pages).",
    "safe_in": "Live-area inset from trim on cover panels, inches (KDP last known 0.125; Ingram 0.25 is safer).",
    "spine_safe_in": "Spine text inset from each fold, inches (KDP last known 0.0625).",
    "barcode_in": "Barcode keep-out [width, height] in inches on the back cover (KDP last known [2.0, 1.2]).",
    "min_pages": "Minimum page count the printer accepts for this format (KDP/Ingram paperback last known 24).",
}

WHERE_TO_LOOK = """How to get correct values:
  1. Confirm with the author: trim size, interior ink (B&W/color), paper (white/cream),
     and which printers (KDP, IngramSpark, other).
  2. Look up the CURRENT requirements for that exact combination:
     - KDP: kdp.amazon.com help pages "Set Trim Size, Bleed, and Margins" and
       "Create a Paperback Cover" / the KDP Cover Calculator.
     - IngramSpark: the IngramSpark File Creation Guide and the Cover Template Generator.
     - Other printers: their file-prep guide; ask the author if none is known.
  3. Write the values into the project's context/book-index.yaml under `production:`
     (schema: book-tools/templates/book-index.template.yaml) and set, per target,
       specs_source: "<page or document you used>"
       specs_verified: YYYY-MM-DD
  4. Re-run the command."""


def _target_path(target: str, key: str) -> str:
    return f"production.print_targets.{target}.{key}"


def require(project: Project, needs: list[str], command: str, targets: list[str] | None = None) -> None:
    """Raise SpecsMissing if any required dotted keys are absent.

    `needs` entries starting with "target." are checked for every target in `targets`;
    others are checked under `production.`.
    """
    def absent(dotted: str) -> bool:
        v = project.get(dotted)
        return v is None or (isinstance(v, (list, tuple)) and any(x is None for x in v))

    missing: list[tuple[str, str]] = []
    for key in needs:
        if not key.startswith("target.") and absent(f"production.{key}"):
            missing.append((f"production.{key}", FIELD_HINTS.get(key, "")))
    for t in targets or []:
        for key in needs:
            sub = key[len("target."):]
            if key.startswith("target.") and absent(_target_path(t, sub)):
                missing.append((_target_path(t, sub), FIELD_HINTS.get(sub, "")))
    if targets is not None and not targets:
        missing.insert(0, ("production.print_targets", "One block per printer, e.g. `kdp:` and `ingram:`."))
    if missing:
        raise SpecsMissing(agent_block(project, command, missing), 2)


def agent_block(project: Project, command: str, missing: list[tuple[str, str]]) -> str:
    width = max(len(k) for k, _ in missing) + 2
    rows = "\n".join(f"  {k:<{width}}{hint}" for k, hint in missing)
    return (
        "=" * 26 + " AGENT ACTION REQUIRED " + "=" * 26 + "\n"
        f"`booktools {command}` needs production specs that are not in\n"
        f"  {project.config_path}\n\n"
        f"Missing:\n{rows}\n\n"
        f"{WHERE_TO_LOOK}\n\n"
        "Values in parentheses are LAST KNOWN references, not guarantees. Do not copy them\n"
        "without checking the printer's current documentation for this trim/paper/ink.\n"
        + "=" * 75
    )


def staleness_warnings(project: Project, targets: list[str]) -> list[str]:
    """Warn (not fail) when a target's specs were never verified or were verified long ago."""
    out = []
    today = dt.date.today()
    for t in targets:
        verified = project.get(_target_path(t, "specs_verified"))
        if verified is None:
            out.append(f"VERIFY: production.print_targets.{t}.specs_verified is not set. Confirm these "
                       f"{t} specs against the printer's current documentation, then record the date.")
            continue
        try:
            d = verified if isinstance(verified, dt.date) else dt.date.fromisoformat(str(verified))
        except ValueError:
            out.append(f"VERIFY: production.print_targets.{t}.specs_verified '{verified}' is not YYYY-MM-DD.")
            continue
        if (today - d).days > STALE_AFTER_DAYS:
            out.append(f"VERIFY: {t} specs last verified {d} (> {STALE_AFTER_DAYS} days). Re-check before upload.")
    return out


def num(value: Any) -> float:
    return float(value)
