"""Retrieval agent: vector search + 1-hop entity-graph expansion, then a local judge that scores
relevance 0-3 and must quote the passage word for word. Only verified, relevant passages are
returned and logged as citable."""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

import networkx as nx
import numpy as np

from ..project import BookToolsError
from .config import ResearchConfig
from .llm import LocalAI
from .store import Store, norm_entity

JUDGE_PROMPT = """You judge whether an excerpt from a source helps answer a research question for a nonfiction book.

Question: {question}

Excerpt:
\"\"\"{excerpt}\"\"\"

Score relevance: 0 = unrelated; 1 = same topic but does not help answer; 2 = partly answers or supports an answer;
3 = directly answers. Then copy the 1-3 sentences that support the answer WORD FOR WORD from the excerpt:
no paraphrase, no added or removed words. If the score is below 2, the quote is "".
Reply with JSON only: {{"score": <0-3>, "quote": "<exact text>", "reason": "<max 15 words>"}}"""

_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        " ": " ", "­": ""})


def _norm_with_map(s: str) -> tuple[str, list[int]]:
    """Lowercased, quote/dash-unified, whitespace-collapsed text + map back to original indices."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(s):
        ch = unicodedata.normalize("NFKC", ch).translate(_TRANS).lower()
        for c in ch:
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) in the original text where `quote` appears (normalized match), else None.

    Leading/trailing quotation marks and ellipses from the model are ignored; anything in the
    middle must match word for word."""
    q, _ = _norm_with_map(quote)
    q = q.strip(" \"'.…")
    if len(q) < 12:
        return None
    t, idx = _norm_with_map(text)
    pos = t.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


def page_at(offsets: list, pos: int, default):
    page = default
    for off, p in offsets:
        if off <= pos and p is not None:
            page = p
    return page


def citation(key: str, page) -> str:
    return f"[@{key}, p. {page}]" if page is not None else f"[@{key}]"


def candidates(store: Store, qvec: np.ndarray, question: str, k: int) -> list[tuple[int, float, str]]:
    ids, mat = store.vectors("source")
    if not ids:
        raise BookToolsError("The research library has no sources yet. Run `python -m booktools research ingest`.", 2)
    sims = mat @ qvec
    order = np.argsort(-sims)
    top = [(ids[i], float(sims[i]), "vector") for i in order[:k]]
    sim_of = {ids[i]: float(sims[i]) for i in range(len(ids))}

    # Graph expansion: entities named in the question + entities of the 3 best chunks, then 1 hop.
    g = nx.Graph()
    g.add_edges_from(store.db.execute("SELECT src, dst FROM relations").fetchall())
    qn = f" {norm_entity(question)} "
    seeds = {eid for eid, norm in store.db.execute("SELECT id, norm FROM entities")
             if len(norm) >= 4 and f" {norm} " in qn}
    for cid, _, _ in top[:3]:
        seeds |= {r[0] for r in store.db.execute("SELECT entity_id FROM entity_chunks WHERE chunk_id=?", (cid,))}
    hood = set(seeds)
    for s in seeds:
        if s in g:
            hood |= set(g.neighbors(s))
    have = {c for c, _, _ in top}
    extra: dict[int, float] = {}
    for eid in hood:
        for (cid,) in store.db.execute("SELECT chunk_id FROM entity_chunks WHERE entity_id=?", (eid,)):
            if cid not in have and cid in sim_of:
                extra[cid] = sim_of[cid]
    graph_hits = sorted(extra.items(), key=lambda x: -x[1])[: max(2, k // 2)]
    return top + [(c, s, "graph") for c, s in graph_hits]


def run(cfg: ResearchConfig, question: str, *, k: int | None = None, chapter: str | None = None,
        as_json: bool = False, judge: bool = True) -> int:
    k = k or cfg.k
    store = Store(cfg.db_path)
    model = store.meta("embed_model") or cfg.models["embed"]
    ai = LocalAI(cfg.endpoint, cfg.gpu_lock, tuple(cfg.models.values()))
    qvec = ai.embed(model, [question])[0]
    cands = candidates(store, qvec, question, k)

    results = []
    if not judge:
        for cid, sim, via in cands:
            c = store.chunk(cid)
            results.append({**_public(c), "similarity": round(sim, 3), "via": via, "score": None,
                            "verified": False, "quote": "", "reason": "unjudged",
                            "cite": citation(c["key"], c["page_start"])})
    else:
        with ai.session(cfg.models["judge"], cfg.context_length):
            for cid, sim, via in cands:
                c = store.chunk(cid)
                verdict = ai.chat_json(cfg.models["judge"], JUDGE_PROMPT.format(
                    question=question, excerpt=c["text"][:5000]), max_tokens=500) or {}
                try:
                    score = int(verdict.get("score", 0)) if isinstance(verdict, dict) else 0
                except (TypeError, ValueError):
                    score = 0
                quote = str(verdict.get("quote") or "") if isinstance(verdict, dict) else ""
                span = locate_quote(c["text"], quote) if quote else None
                page = page_at(c["offsets"], span[0], c["page_start"]) if span else c["page_start"]
                if span:  # store the source's own words, not the model's copy
                    quote = re.sub(r"\s+", " ", c["text"][span[0]:span[1]]).strip()
                results.append({**_public(c), "similarity": round(sim, 3), "via": via, "score": score,
                                "verified": span is not None, "quote": quote,
                                "reason": str(verdict.get("reason") or "") if isinstance(verdict, dict) else "no JSON",
                                "page": page, "cite": citation(c["key"], page)})

    approved = [r for r in results if r["verified"] and (r["score"] or 0) >= cfg.judge_min_score]
    rejected = [r for r in results if r not in approved]
    approved.sort(key=lambda r: (-(r["score"] or 0), -r["similarity"]))

    if judge and approved:
        with cfg.citations_log.open("a", encoding="utf-8") as f:
            for r in approved:
                f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "question": question,
                                    "chapter": chapter, "key": r["key"], "page": r["page"], "quote": r["quote"],
                                    "chunk_id": r["chunk_id"], "score": r["score"]}, ensure_ascii=False) + "\n")
    store.close()

    if as_json:
        print(json.dumps({"question": question, "approved": approved,
                          "rejected": [{k_: r[k_] for k_ in ("key", "page_start", "score", "verified", "reason", "via")}
                                       for r in rejected]}, ensure_ascii=False, indent=2))
        return 0
    print(f"Question: {question}\n")
    if not judge:
        for n, r in enumerate(results, 1):
            print(f"{n}. {r['cite']}  sim {r['similarity']}  via {r['via']}  (UNJUDGED - not citable)\n   {r['text'][:300]}...\n")
        return 0
    if not approved:
        print("No passage passed the judge. Do not write this claim from memory: rephrase the question,\n"
              "add sources to research/inbox/, or mark the gap as [AUTHOR: need source for ...].")
    for n, r in enumerate(approved, 1):
        print(f"{n}. {r['cite']}  score {r['score']}/3  via {r['via']}  - {r['title']}\n"
              f"   \"{r['quote']}\"\n   why: {r['reason']}\n")
    unverified = [r for r in rejected if (r["score"] or 0) >= cfg.judge_min_score and not r["verified"]]
    print(f"Rejected {len(rejected)} candidate(s)" + (f", {len(unverified)} of them because the quote was UNVERIFIED "
                                                       "(not found word for word in the source)" if unverified else "") + ".")
    return 0


def _public(c: dict) -> dict:
    return {k: c[k] for k in ("chunk_id", "key", "title", "page_start", "page_end", "text", "path", "url")}
