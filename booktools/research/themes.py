"""Brain Dump -> Cluster -> Outline.

Agglomerative clustering (cosine, average linkage) groups the author's notes into chapter themes;
the requested range is searched and the best silhouette wins. Sub-clusters become suggested
sections. HDBSCAN then flags notes that fit no theme (density noise that is also far from its
cluster centroid), and tiny clusters are treated the same way: they go to "Unplaced ideas"."""
from __future__ import annotations

import datetime as dt

import numpy as np

from ..project import BookToolsError
from .config import ResearchConfig
from .llm import LocalAI
from .store import Store

NAME_PROMPT = """These notes were clustered into one chapter theme for a nonfiction book, grouped into sections.
Name the chapter and each section. Titles: plain, specific, max 8 words, no colons.
Reply with JSON only: {{"chapter": "...", "summary": "<one sentence>", "sections": ["<title for S1>", "<title for S2>", ...]}}

{notes}"""


def _parse_range(spec: str) -> tuple[int, int]:
    a, _, b = spec.partition("..")
    lo, hi = int(a), int(b or a)
    if lo < 2 or hi < lo:
        raise BookToolsError("--chapters must look like 8..12 (min 2).", 2)
    return lo, hi


def cluster(X: np.ndarray, lo: int, hi: int) -> tuple[np.ndarray, int, float]:
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score
    best = (None, 0, -2.0)
    for k in range(lo, min(hi, len(X) - 1) + 1):
        labels = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(X)
        if len(set(labels)) < 2:
            continue
        s = float(silhouette_score(X, labels, metric="cosine"))
        if s > best[2]:
            best = (labels, k, s)
    if best[0] is None:
        raise BookToolsError("Not enough distinct notes to cluster.", 2)
    return best


def min_chapter_size(n: int) -> int:
    return max(3, round(n * 0.05))


def unplaced_mask(X: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """A note is unplaced if its cluster is too small to be a chapter, or if HDBSCAN calls it
    noise AND it is a clear outlier inside its own cluster (z-score of centroid similarity)."""
    from sklearn.cluster import HDBSCAN
    n = len(X)
    noise = HDBSCAN(min_cluster_size=max(3, n // 25), metric="euclidean", copy=True).fit_predict(X) == -1
    outlier = np.zeros(n, dtype=bool)
    for lab in set(labels):
        m = labels == lab
        c = X[m].mean(axis=0)
        c /= np.linalg.norm(c) or 1
        sims = X[m] @ c
        if m.sum() >= 4:
            z = (sims - sims.mean()) / (sims.std() or 1)
            outlier[np.where(m)[0]] = z < -1.5
    sizes = {lab: int((labels == lab).sum()) for lab in set(labels)}
    tiny = np.array([sizes[lab] < min_chapter_size(n) for lab in labels])
    return (noise & outlier) | tiny


def sections_for(X: np.ndarray, members: list[int]) -> list[list[int]]:
    """Split a chapter into 2-4 sections; one-note sections are merged into the nearest section."""
    from sklearn.cluster import AgglomerativeClustering
    n_sec = min(4, len(members) // 3)
    if n_sec < 2:
        return [members]
    sub = AgglomerativeClustering(n_clusters=n_sec, metric="cosine", linkage="average").fit_predict(X[members])
    secs = {s: [members[j] for j in range(len(members)) if sub[j] == s] for s in set(sub)}
    while True:
        singles = [s for s, v in secs.items() if len(v) < 2]
        if not singles or len(secs) <= 1:
            break
        s = singles[0]
        lone = secs.pop(s)[0]
        cents = {t: X[v].mean(axis=0) for t, v in secs.items()}
        best = max(cents, key=lambda t: float(X[lone] @ cents[t]) / (np.linalg.norm(cents[t]) or 1))
        secs[best].append(lone)
    return [v for v in secs.values() if v] if len(secs) > 1 else [members]


def run(cfg: ResearchConfig, chapters: str = "8..12", *, llm: bool = True) -> int:
    lo, hi = _parse_range(chapters)
    store = Store(cfg.db_path)
    ids, X = store.vectors("note")
    if len(ids) < max(6, lo + 2):
        store.close()
        raise BookToolsError(
            f"Only {len(ids)} note(s) in the library. Put brain dumps/notes in context/raw-material/ "
            "and run `python -m booktools research ingest --notes` first.", 2)
    notes = [store.db.execute("SELECT c.text, s.path FROM chunks c JOIN sources s ON s.id=c.source_id WHERE c.id=?",
                              (i,)).fetchone() for i in ids]
    labels, k, sil = cluster(X, lo, hi)
    out_mask = unplaced_mask(X, labels)

    groups = []
    for lab in sorted(set(labels[~out_mask]), key=lambda l: -int(((labels == l) & ~out_mask).sum())):
        members = [i for i in range(len(ids)) if labels[i] == lab and not out_mask[i]]
        groups.append({"sections": sections_for(X, members)})

    ai = LocalAI(cfg.endpoint, cfg.gpu_lock, tuple(cfg.models.values()))
    if llm:
        with ai.session(cfg.models["extractor"], cfg.context_length):
            for g in groups:
                text = "\n".join(f"S{s + 1}: - " + "\n    - ".join(notes[m][0][:240] for m in sec[:8])
                                 for s, sec in enumerate(g["sections"]))
                data = ai.chat_json(cfg.models["extractor"], NAME_PROMPT.format(notes=text), max_tokens=400) or {}
                g["title"] = str(data.get("chapter") or "") if isinstance(data, dict) else ""
                g["summary"] = str(data.get("summary") or "") if isinstance(data, dict) else ""
                g["section_titles"] = list(data.get("sections") or []) if isinstance(data, dict) else []
    store.close()

    lines = [f"# Themes from raw material", "",
             f"Generated {dt.date.today().isoformat()} by `booktools research themes --chapters {chapters}` "
             f"from {len(ids)} notes. Chose {len(groups)} chapter themes (searched {lo}-{hi}; silhouette {sil:.2f}).",
             "Order is by size, not reading order. Reorder, merge, and rename freely, then build the TOC.", ""]
    for n, g in enumerate(groups, 1):
        lines += [f"## Theme {n}: {g.get('title') or f'Untitled theme {n}'}", ""]
        if g.get("summary"):
            lines += [g["summary"], ""]
        titles = g.get("section_titles") or []
        for s, sec in enumerate(g["sections"]):
            if len(g["sections"]) > 1:
                lines += [f"### {titles[s] if s < len(titles) and titles[s] else f'Section {s + 1}'}", ""]
            lines += [f"- {notes[m][0][:300]}  _({notes[m][1]})_" for m in sec] + [""]
    lines += ["## Unplaced ideas", "",
              "These fit no theme. Decide: cut, park for another book, or build a chapter around them.", ""]
    lines += [f"- {notes[i][0][:300]}  _({notes[i][1]})_" for i in range(len(ids)) if out_mask[i]] or ["- (none)"]
    out = cfg.project.root / "context" / "themes.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(groups)} themes, {int(out_mask.sum())} unplaced ideas -> {out.relative_to(cfg.project.root)}")
    return 0
