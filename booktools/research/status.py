"""`research status` and `research graph` (build missing graph / export GraphML)."""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from .config import ResearchConfig
from .ingest import load_sources
from .llm import LocalAI
from .store import Store


def status(cfg: ResearchConfig) -> int:
    if not cfg.db_path.is_file():
        print("No research library yet. Drop files into research/inbox/ (or URLs into research/urls.txt) and run:\n"
              "  python -m booktools research ingest")
        return 0
    store = Store(cfg.db_path)
    c = store.counts()
    model = store.meta("embed_model")
    store.close()
    _, entries = load_sources(cfg)
    unverified = [e["key"] for e in entries if not e.get("verified")]
    approved = len(cfg.citations_log.read_text(encoding="utf-8").splitlines()) if cfg.citations_log.is_file() else 0
    print(f"Library:     {cfg.db_path.relative_to(cfg.project.root)}  (embedding model: {model})")
    print(f"Sources:     {c['sources']}   Notes: {c['notes']}   Chunks: {c['chunks']} "
          f"({c['chunks_graphed']} graphed)")
    print(f"Graph:       {c['entities']} entities, {c['relations']} relations")
    print(f"Approved:    {approved} judge-approved passages logged")
    print(f"Models:      " + ", ".join(f"{k}={v}" for k, v in cfg.models.items()) + f"  @ {cfg.endpoint}")
    if unverified:
        print(f"Unverified:  {len(unverified)} source(s) need metadata checks in sources.yaml: "
              + ", ".join(unverified[:8]) + (" ..." if len(unverified) > 8 else ""))
    return 0


def graph(cfg: ResearchConfig, export: Path | None = None) -> int:
    from .ingest import build_graph
    store = Store(cfg.db_path)
    done = build_graph(cfg, LocalAI(cfg.endpoint, cfg.gpu_lock, tuple(cfg.models.values())), store)
    if done:
        print(f"Graphed {done} chunk(s).")
    if export:
        g = nx.MultiDiGraph()
        for eid, name, type_ in store.db.execute("SELECT id, name, type FROM entities"):
            g.add_node(eid, label=name, type=type_ or "")
        for a, b, label, cid in store.db.execute("SELECT src, dst, label, chunk_id FROM relations"):
            g.add_edge(a, b, label=label, chunk=cid)
        export.parent.mkdir(parents=True, exist_ok=True)
        nx.write_graphml(g, export)
        print(f"Exported {g.number_of_nodes()} entities / {g.number_of_edges()} relations -> {export}")
    store.close()
    return 0
