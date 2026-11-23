"""Bibliography: audit every [@key, p. N] citation in the manuscript, then write CSL-JSON for pandoc."""
from __future__ import annotations

import json
import re

from .config import ResearchConfig
from .ingest import load_sources

BRACKET = re.compile(r"\[([^\[\]]*@[^\[\]]*)\]")
ITEM = re.compile(r"-?@([A-Za-z0-9_][\w:.#$%&\-+?<>~/]*)\s*(?:,\s*(.*))?")
PAGE = re.compile(r"\bpp?\.\s*([\w\-–]+)")


def citations_in(cfg: ResearchConfig) -> list[dict]:
    out = []
    for c in cfg.project.chapters():
        for line_no, line in enumerate(c.body.splitlines(), 1):
            for m in BRACKET.finditer(line):
                for part in m.group(1).split(";"):
                    im = ITEM.search(part.strip())
                    if im:
                        pm = PAGE.search(im.group(2) or "")
                        out.append({"file": c.path.name, "line": line_no, "key": im.group(1),
                                    "page": pm.group(1) if pm else None})
    return out


def approved_log(cfg: ResearchConfig) -> set[tuple[str, str | None]]:
    seen: set[tuple[str, str | None]] = set()
    if cfg.citations_log.is_file():
        for line in cfg.citations_log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add((r["key"], str(r["page"]) if r.get("page") is not None else None))
    return seen


def run(cfg: ResearchConfig, *, strict: bool = False, include_all: bool = False) -> int:
    _, entries = load_sources(cfg)
    by_key = {e.get("key"): e for e in entries if e.get("key")}
    cites = citations_in(cfg)
    approved = approved_log(cfg)
    approved_keys = {k for k, _ in approved}

    missing, unverified, unapproved = [], set(), []
    for c in cites:
        if c["key"] not in by_key:
            missing.append(c)
            continue
        if not by_key[c["key"]].get("verified"):
            unverified.add(c["key"])
        page_ok = (c["key"], c["page"]) in approved or (c["page"] is None and c["key"] in approved_keys)
        if not page_ok:
            unapproved.append(c)

    used = sorted({c["key"] for c in cites if c["key"] in by_key})
    keys = sorted(k for k, e in by_key.items() if e.get("verified")) if include_all else used
    refs = []
    for k in keys:
        csl = dict(by_key[k].get("csl") or {})
        csl.setdefault("type", "book")
        if by_key[k].get("url") and "URL" not in csl:
            csl["URL"] = by_key[k]["url"]
        refs.append({"id": k, **csl})
    cfg.references_json.write_text(json.dumps(refs, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Citations found: {len(cites)} ({len(used)} distinct sources) -> "
          f"{cfg.references_json.relative_to(cfg.project.root)} ({len(refs)} entries)")
    for c in missing:
        print(f"  MISSING KEY      {c['file']}:{c['line']}  @{c['key']}  (not in research/sources.yaml)")
    for k in sorted(unverified):
        print(f"  UNVERIFIED       @{k}  (check its metadata in sources.yaml, then set verified: true)")
    for c in unapproved:
        print(f"  NOT JUDGE-APPROVED {c['file']}:{c['line']}  @{c['key']}" + (f", p. {c['page']}" if c["page"] else "")
              + "  (no approved `research query` passage at this page)")
    if not (missing or unverified or unapproved):
        print("  All citations resolve to verified sources and judge-approved passages.")
    if missing or (strict and (unverified or unapproved)):
        return 2
    return 0
