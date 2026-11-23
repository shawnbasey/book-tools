"""SQLite store for one book: sources, chunks (with vectors), and the entity graph."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import numpy as np

from ..project import BookToolsError

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, sha256 TEXT UNIQUE, kind TEXT NOT NULL,  -- kind: source | note
  path TEXT, url TEXT, title TEXT, pages INTEGER, added TEXT, csl TEXT);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  ord INTEGER, page_start INTEGER, page_end INTEGER, offsets TEXT, text TEXT NOT NULL, vector BLOB,
  graphed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY, name TEXT, norm TEXT UNIQUE, type TEXT);
CREATE TABLE IF NOT EXISTS entity_chunks (entity_id INTEGER, chunk_id INTEGER, PRIMARY KEY (entity_id, chunk_id));
CREATE TABLE IF NOT EXISTS relations (src INTEGER, dst INTEGER, label TEXT, chunk_id INTEGER,
  PRIMARY KEY (src, dst, label, chunk_id));
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS ec_chunk ON entity_chunks(chunk_id);
"""


def norm_entity(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", "", name.lower())).strip()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # ---- meta ---------------------------------------------------------------
    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def check_embed_model(self, model: str) -> None:
        """An index is fixed to the embedding model it was built with."""
        current = self.meta("embed_model")
        if current is None:
            self.set_meta("embed_model", model)
        elif current != model:
            raise BookToolsError(
                f"This library was built with embedding model '{current}', but config now says '{model}'.\n"
                "  Queries must use the original model. To switch, delete research/library.sqlite and re-ingest.", 2)

    # ---- sources / chunks -----------------------------------------------------
    def has_sha(self, sha: str) -> bool:
        return self.db.execute("SELECT 1 FROM sources WHERE sha256=?", (sha,)).fetchone() is not None

    def has_url(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM sources WHERE url=?", (url,)).fetchone() is not None

    def keys(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT key FROM sources")}

    def add_source(self, **f) -> int:
        cols = ",".join(f)
        cur = self.db.execute(f"INSERT INTO sources ({cols}) VALUES ({','.join('?' * len(f))})", tuple(f.values()))
        return int(cur.lastrowid)

    def add_chunk(self, source_id: int, ord_: int, text: str, page_start, page_end, offsets, vector: np.ndarray) -> int:
        cur = self.db.execute(
            "INSERT INTO chunks (source_id, ord, page_start, page_end, offsets, text, vector) VALUES (?,?,?,?,?,?,?)",
            (source_id, ord_, page_start, page_end, json.dumps(offsets), text, vector.astype(np.float32).tobytes()))
        return int(cur.lastrowid)

    def vectors(self, kind: str) -> tuple[list[int], np.ndarray]:
        rows = self.db.execute(
            "SELECT c.id, c.vector FROM chunks c JOIN sources s ON s.id=c.source_id WHERE s.kind=? ORDER BY c.id",
            (kind,)).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        return [r[0] for r in rows], np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])

    def chunk(self, cid: int) -> dict:
        r = self.db.execute(
            "SELECT c.id, c.text, c.page_start, c.page_end, c.offsets, s.key, s.title, s.path, s.url "
            "FROM chunks c JOIN sources s ON s.id=c.source_id WHERE c.id=?", (cid,)).fetchone()
        return {"chunk_id": r[0], "text": r[1], "page_start": r[2], "page_end": r[3],
                "offsets": json.loads(r[4] or "[]"), "key": r[5], "title": r[6], "path": r[7], "url": r[8]}

    # ---- graph -----------------------------------------------------------------
    def upsert_entity(self, name: str, type_: str) -> int | None:
        n = norm_entity(name)
        if len(n) < 2:
            return None
        self.db.execute("INSERT OR IGNORE INTO entities (name, norm, type) VALUES (?,?,?)", (name.strip(), n, type_))
        return int(self.db.execute("SELECT id FROM entities WHERE norm=?", (n,)).fetchone()[0])

    def link(self, entity_id: int, chunk_id: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO entity_chunks VALUES (?,?)", (entity_id, chunk_id))

    def relate(self, a: int, b: int, label: str, chunk_id: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?)", (a, b, label[:60], chunk_id))

    def counts(self) -> dict[str, int]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "sources": q("SELECT COUNT(*) FROM sources WHERE kind='source'"),
            "notes": q("SELECT COUNT(*) FROM sources WHERE kind='note'"),
            "chunks": q("SELECT COUNT(*) FROM chunks"),
            "chunks_graphed": q("SELECT COUNT(*) FROM chunks WHERE graphed=1"),
            "entities": q("SELECT COUNT(*) FROM entities"),
            "relations": q("SELECT COUNT(*) FROM relations"),
        }
