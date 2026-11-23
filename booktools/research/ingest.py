"""Ingest sources (research/inbox/, research/urls.txt) and raw notes (context/raw-material/).

PDF -> page-aware chunks; HTML/web pages -> main text via trafilatura; DOCX; MD/TXT.
Each new source gets a draft citation entry in research/sources.yaml (verified: false).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

import yaml

from ..project import BookToolsError
from .config import ResearchConfig
from .llm import LocalAI
from .store import Store

TEXT_EXT = {".md", ".markdown", ".txt"}
HTML_EXT = {".html", ".htm"}
SKIP = {".gitkeep", ".ds_store", "thumbs.db", "desktop.ini"}
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

META_PROMPT = """Draft a bibliography entry for this source as CSL-JSON. Use ONLY facts visible below; use null
when unknown. Never invent authors or dates.
Reply with JSON only, shaped like:
{{"type": "book|article-journal|article-newspaper|webpage|report|chapter|thesis|post",
  "title": "...", "author": [{{"family": "...", "given": "..."}}], "issued": {{"date-parts": [[YYYY]]}},
  "publisher": null, "container-title": null, "URL": null, "DOI": null}}

File name: {name}
Embedded metadata: {meta}
Opening text:
\"\"\"{text}\"\"\""""

GRAPH_PROMPT = """Extract the key named entities and relations from this passage for a knowledge graph.
Entities: people, organizations, works, concepts, methods, places, events (max 12, most important first).
Relations: short verb phrases between two listed entities (max 8).
Reply with JSON only:
{{"entities": [{{"name": "...", "type": "person|org|work|concept|method|place|event"}}],
  "relations": [{{"source": "...", "target": "...", "label": "..."}}]}}

Passage:
\"\"\"{text}\"\"\""""


# ---------------------------------------------------------------- extraction
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(path: Path) -> tuple[list[tuple[int | None, str]], dict]:
    """Return ([(page_or_None, text)], metadata)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        import fitz
        with fitz.open(str(path)) as doc:
            pages = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
            meta = {k: v for k, v in (doc.metadata or {}).items() if v and k in ("title", "author", "creationDate", "subject")}
        return pages, meta
    if ext in HTML_EXT:
        import trafilatura
        raw = path.read_text(encoding="utf-8", errors="replace")
        doc = trafilatura.bare_extraction(raw, with_metadata=True, include_comments=False)
        if not doc:
            raise BookToolsError(f"Could not extract readable text from {path.name}")
        d = doc if isinstance(doc, dict) else doc.as_dict()
        meta = {k: d.get(k) for k in ("title", "author", "date", "sitename", "url") if d.get(k)}
        return [(None, d.get("text") or "")], meta
    if ext == ".docx":
        try:
            import docx  # python-docx
        except ImportError:
            raise BookToolsError("DOCX support needs python-docx:  python -m pip install python-docx") from None
        d = docx.Document(str(path))
        cp = d.core_properties
        meta = {k: v for k, v in {"title": cp.title, "author": cp.author,
                                  "date": cp.created.isoformat() if cp.created else None}.items() if v}
        return [(None, "\n\n".join(p.text for p in d.paragraphs))], meta
    if ext in TEXT_EXT:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = {}
        fm = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
        if fm:
            try:
                meta = {k: v for k, v in (yaml.safe_load(fm.group(1)) or {}).items() if isinstance(v, (str, int))}
            except yaml.YAMLError:
                pass
            text = text[fm.end():]
        return [(None, text)], meta
    raise BookToolsError(f"Unsupported file type: {path.name} (pdf, html, docx, md, txt)")


# ---------------------------------------------------------------- chunking
def _paragraphs(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    out = []
    for page, text in pages:
        text = text.replace("\r", "")
        for para in re.split(r"\n\s*\n", text):
            para = re.sub(r"-\n(?=[a-z])", "", para)            # de-hyphenate PDF line breaks
            para = re.sub(r"\s*\n\s*", " ", para).strip()
            if para:
                out.append((page, para))
    return out


def chunk_source(pages, target_words: int) -> list[dict]:
    """Group paragraphs into ~target_words chunks, keeping page offsets for quote -> page lookup."""
    chunks, cur, words = [], [], 0

    def flush():
        nonlocal cur, words
        if not cur:
            return
        text, offsets = "", []
        for page, para in cur:
            if text:
                text += "\n\n"
            offsets.append([len(text), page])
            text += para
        pages = [p for p, _ in cur if p is not None]
        chunks.append({"text": text, "offsets": offsets,
                       "page_start": min(pages) if pages else None, "page_end": max(pages) if pages else None})
        cur, words = [], 0

    for page, para in _paragraphs(pages):
        n = len(para.split())
        if n > target_words * 1.5:          # split very long paragraphs on sentence boundaries
            sents, buf = re.split(r"(?<=[.!?])\s+", para), []
            for s in sents:
                buf.append(s)
                if len(" ".join(buf).split()) >= target_words:
                    flush(); cur.append((page, " ".join(buf))); flush(); buf = []  # noqa: E702
            if buf:
                cur.append((page, " ".join(buf))); words += len(" ".join(buf).split())  # noqa: E702
            continue
        if words + n > target_words and cur:
            flush()
        cur.append((page, para))
        words += n
    flush()
    return chunks


def chunk_notes(text: str, max_words: int = 150) -> list[dict]:
    """One chunk per idea: bullets and paragraphs are separate ideas."""
    ideas, buf = [], []
    for line in text.replace("\r", "").split("\n"):
        if BULLET.match(line):
            if buf:
                ideas.append(" ".join(buf)); buf = []  # noqa: E702
            buf.append(BULLET.sub("", line).strip())
        elif not line.strip():
            if buf:
                ideas.append(" ".join(buf)); buf = []  # noqa: E702
        elif not line.lstrip().startswith("#"):
            buf.append(line.strip())
    if buf:
        ideas.append(" ".join(buf))
    out = []
    for idea in ideas:
        if len(idea.split()) < 3:
            continue
        out += chunk_source([(None, idea)], max_words) if len(idea.split()) > max_words else \
            [{"text": idea, "offsets": [[0, None]], "page_start": None, "page_end": None}]
    return out


# ---------------------------------------------------------------- citation keys / sources.yaml
def make_key(csl: dict, fallback: str, taken: set[str]) -> str:
    authors = csl.get("author") or []
    family = (authors[0].get("family") or authors[0].get("literal") or "") if authors else ""
    year = ""
    try:
        year = str(csl["issued"]["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        pass
    words = [w for w in re.findall(r"[a-z0-9]+", (csl.get("title") or "").lower()) if w not in
             {"the", "a", "an", "of", "on", "in", "and", "for", "to", "how", "why", "what"}]
    base = re.sub(r"[^a-z0-9]", "", (family or (words[0] if words else fallback)).lower())[:20] + year
    if words and family:
        base += words[0][:12]
    base = base or re.sub(r"[^a-z0-9]", "", fallback.lower())[:24] or "source"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}{chr(96 + n)}", n + 1     # smith2024b, smith2024c ...
    taken.add(key)
    return key


def load_sources(cfg: ResearchConfig) -> tuple[list[str], list[dict]]:
    """(header comment lines, entries)."""
    if not cfg.sources_yaml.is_file():
        return ["# Bibliographic metadata for research sources. Edit freely; set verified: true once checked."], []
    text = cfg.sources_yaml.read_text(encoding="utf-8")
    header = []
    for line in text.splitlines():
        if line.startswith("#"):
            header.append(line)
        else:
            break
    data = yaml.safe_load(text) or {}
    return header, list(data.get("sources") or [])


def save_sources(cfg: ResearchConfig, header: list[str], entries: list[dict]) -> None:
    body = yaml.safe_dump({"sources": entries}, sort_keys=False, allow_unicode=True, width=100)
    cfg.sources_yaml.write_text("\n".join(header) + "\n" + body, encoding="utf-8")


def _year(value) -> list | None:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", str(value or ""))
    return [[int(m.group(1))]] if m else None


def fallback_csl(meta: dict, path: Path, url: str | None) -> dict:
    csl = {"type": "webpage" if url or path.suffix.lower() in HTML_EXT else "book",
           "title": meta.get("title") or path.stem.replace("-", " ").replace("_", " ")}
    if meta.get("author"):
        name = str(meta["author"]).split(";")[0].split(",")[0].strip()
        parts = name.split()
        csl["author"] = [{"family": parts[-1], "given": " ".join(parts[:-1])}] if len(parts) > 1 else [{"literal": name}]
    issued = _year(meta.get("date") or meta.get("creationDate"))
    if issued:
        csl["issued"] = {"date-parts": issued}
    if url or meta.get("url"):
        csl["URL"] = url or meta.get("url")
    if meta.get("sitename"):
        csl["container-title"] = meta["sitename"]
    return csl


# ---------------------------------------------------------------- web pages
def fetch_urls(cfg: ResearchConfig, store: Store) -> list[tuple[Path, str]]:
    if not cfg.urls_txt.is_file():
        return []
    import trafilatura
    out = []
    web_dir = cfg.inbox / "web"
    for line in cfg.urls_txt.read_text(encoding="utf-8").splitlines():
        url = line.split("#", 1)[0].strip()
        if not url or store.has_url(url):
            continue
        html = trafilatura.fetch_url(url)
        if not html:
            print(f"  ! could not fetch {url}")
            continue
        web_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("://", 1)[-1])[:60].strip("-")
        path = web_dir / f"{slug}-{sha256(url.encode())[:8]}.html"
        path.write_text(html, encoding="utf-8")
        out.append((path, url))
        print(f"  fetched {url} -> {path.relative_to(cfg.project.root)}")
    return out


# ---------------------------------------------------------------- main entry
def run(cfg: ResearchConfig, *, notes: bool = False, graph: bool = True, llm: bool = True) -> int:
    ai = LocalAI(cfg.endpoint, cfg.gpu_lock, tuple(cfg.models.values()))
    store = Store(cfg.db_path)
    store.check_embed_model(cfg.models["embed"])
    header, entries = load_sources(cfg)
    taken = store.keys() | {e.get("key") for e in entries if e.get("key")}

    jobs: list[tuple[Path, str | None, str]] = []      # (path, url, kind)
    if notes:
        if cfg.notes_dir.is_dir():
            jobs += [(p, None, "note") for p in sorted(cfg.notes_dir.rglob("*"))
                     if p.is_file() and p.name.lower() not in SKIP and p.suffix.lower() in TEXT_EXT | {".docx"}]
    else:
        cfg.inbox.mkdir(parents=True, exist_ok=True)
        jobs += [(p, u, "source") for p, u in fetch_urls(cfg, store)]
        fetched = {p for p, _, _ in jobs}
        jobs += [(p, None, "source") for p in sorted(cfg.inbox.rglob("*"))
                 if p.is_file() and p.name.lower() not in SKIP and p not in fetched]

    new: list[dict] = []
    for path, url, kind in jobs:
        data = path.read_bytes()
        digest = sha256(data)
        if store.has_sha(digest):
            continue
        try:
            pages, meta = extract(path)
        except BookToolsError as exc:
            print(f"  ! skipped {path.name}: {exc}")
            continue
        chunks = chunk_notes("\n\n".join(t for _, t in pages)) if kind == "note" else chunk_source(pages, cfg.chunk_words)
        if not chunks:
            print(f"  ! no text in {path.name}")
            continue
        new.append({"path": path, "url": url or meta.get("url"), "kind": kind, "sha": digest,
                    "pages": len(pages) if path.suffix.lower() == ".pdf" else None, "meta": meta, "chunks": chunks,
                    "opening": " ".join(t for _, t in pages)[:2500]})

    if not new:
        print("Nothing new to ingest.")
        store.close()
        return 0

    total = sum(len(n["chunks"]) for n in new)
    print(f"Embedding {total} chunks from {len(new)} new file(s) with {cfg.models['embed']}...")
    vectors = ai.embed(cfg.models["embed"], [c["text"] for n in new for c in n["chunks"]])

    if llm and any(n["kind"] == "source" for n in new):
        with ai.session(cfg.models["extractor"], cfg.context_length):
            for n in (x for x in new if x["kind"] == "source"):
                csl = ai.chat_json(cfg.models["extractor"], META_PROMPT.format(
                    name=n["path"].name, meta=n["meta"], text=n["opening"])) or {}
                base = fallback_csl(n["meta"], n["path"], n["url"])
                n["csl"] = {**base, **{k: v for k, v in csl.items() if v}} if isinstance(csl, dict) else base
    for n in new:
        n.setdefault("csl", fallback_csl(n["meta"], n["path"], n["url"]))

    i = 0
    rel = lambda p: p.relative_to(cfg.project.root).as_posix()  # noqa: E731
    for n in new:
        key = make_key(n["csl"], n["path"].stem, taken) if n["kind"] == "source" else f"note-{n['sha'][:10]}"
        n["key"] = key
        sid = store.add_source(key=key, sha256=n["sha"], kind=n["kind"], path=rel(n["path"]), url=n["url"],
                               title=n["csl"].get("title") if n["kind"] == "source" else n["path"].stem,
                               pages=n["pages"], added=dt.date.today().isoformat(), csl=yaml.safe_dump(n["csl"]))
        n["chunk_ids"] = []
        for o, c in enumerate(n["chunks"]):
            n["chunk_ids"].append(store.add_chunk(sid, o, c["text"], c["page_start"], c["page_end"], c["offsets"], vectors[i]))
            i += 1
        if n["kind"] == "source":
            entries.append({"key": key, "file": rel(n["path"]), **({"url": n["url"]} if n["url"] else {}),
                            "verified": False, "csl": n["csl"]})
        print(f"  + {key:<28} {len(n['chunks']):>4} chunks  {n['path'].name}")
    store.db.commit()
    if any(n["kind"] == "source" for n in new):
        save_sources(cfg, header, entries)

    if graph and llm:
        build_graph(cfg, ai, store)
    store.close()
    print(f"\nDone. Check the new entries in {cfg.sources_yaml.relative_to(cfg.project.root)} and set verified: true.")
    return 0


def build_graph(cfg: ResearchConfig, ai: LocalAI, store: Store) -> int:
    rows = store.db.execute(
        "SELECT c.id, c.text FROM chunks c JOIN sources s ON s.id=c.source_id WHERE s.kind='source' AND c.graphed=0"
    ).fetchall()
    if not rows:
        return 0
    print(f"Extracting entity graph from {len(rows)} chunks with {cfg.models['extractor']}...")
    with ai.session(cfg.models["extractor"], cfg.context_length):
        for n, (cid, text) in enumerate(rows, 1):
            data = ai.chat_json(cfg.models["extractor"], GRAPH_PROMPT.format(text=text[:6000]), max_tokens=900) or {}
            ids = {}
            for e in (data.get("entities") or [])[:12] if isinstance(data, dict) else []:
                if isinstance(e, dict) and e.get("name"):
                    eid = store.upsert_entity(str(e["name"]), str(e.get("type") or "concept"))
                    if eid:
                        ids[str(e["name"]).lower()] = eid
                        store.link(eid, cid)
            for r in (data.get("relations") or [])[:8] if isinstance(data, dict) else []:
                if isinstance(r, dict):
                    a, b = ids.get(str(r.get("source", "")).lower()), ids.get(str(r.get("target", "")).lower())
                    if a and b and a != b:
                        store.relate(a, b, str(r.get("label") or "related to"), cid)
            store.db.execute("UPDATE chunks SET graphed=1 WHERE id=?", (cid,))
            if n % 25 == 0:
                store.db.commit()
                print(f"  {n}/{len(rows)}")
    store.db.commit()
    return len(rows)
