"""The `research:` block of a book's book-index.yaml, plus model-role resolution via MODELS.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..project import BookToolsError, Project, SpecsMissing

DEFAULTS = {
    "endpoint": "http://localhost:1234/v1",   # LM Studio's default; set your own port in config
    "chunk_words": 300,
    "judge_min_score": 2,
    "k": 8,
    "context_length": 8192,
    "citation_style": "chicago-notes-bibliography",
}
MODEL_KEYS = {
    "embed": "Embedding model (role like embed.book, or an LM Studio model id).",
    "extractor": "Model that drafts citation metadata, extracts the entity graph, and names themes.",
    "judge": "Model that scores each retrieved passage 0-3 and quotes it word for word.",
}
# Same row format as a Projects-style MODELS.md: | `role` | vendor | use | `model-id` | agy | premium |
ROLE_ROW = re.compile(r"^\|\s*`([a-z_.]+)`\s*\|([^|]*)\|([^|]*)\|\s*`([^`]+)`\s*\|")


@dataclass
class ResearchConfig:
    project: Project
    endpoint: str
    models: dict[str, str]           # resolved model ids
    roles: dict[str, str]            # as written in config
    chunk_words: int
    judge_min_score: int
    k: int
    context_length: int
    citation_style: str
    gpu_lock: Path | None
    models_md: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return self.project.root / "research"

    @property
    def inbox(self) -> Path:
        return self.dir / "inbox"

    @property
    def db_path(self) -> Path:
        return self.dir / "library.sqlite"

    @property
    def sources_yaml(self) -> Path:
        return self.dir / "sources.yaml"

    @property
    def urls_txt(self) -> Path:
        return self.dir / "urls.txt"

    @property
    def citations_log(self) -> Path:
        return self.dir / "citations.jsonl"

    @property
    def references_json(self) -> Path:
        return self.dir / "references.json"

    @property
    def notes_dir(self) -> Path:
        return self.project.root / str(self.extra.get("notes_dir") or "context/raw-material")


def find_models_md(start: Path) -> Path | None:
    for d in (start, *start.parents):
        p = d / "MODELS.md"
        if p.is_file():
            return p
    return None


def role_table(models_md: Path | None) -> dict[str, str]:
    if not models_md:
        return {}
    out = {}
    for line in models_md.read_text(encoding="utf-8").splitlines():
        m = ROLE_ROW.match(line.strip())
        if m:
            out[m.group(1)] = m.group(4)
    return out


def load(project: Project, command: str) -> ResearchConfig:
    block = project.config.get("research") or {}
    models_cfg = block.get("models") or {}
    missing = [(f"research.models.{k}", hint) for k, hint in MODEL_KEYS.items() if not models_cfg.get(k)]
    if missing:
        from ..specs import agent_block
        raise SpecsMissing(agent_block(project, command, missing).replace(
            "How to get correct values:", "How to fill these in:\n  Pick models available in LM Studio (`lms ls`) or roles from MODELS.md.\n"
            "  Embedding: a dedicated embedding model. Extractor/judge: an instruct model that follows JSON.\n"
            "Printer-spec guidance below does not apply to model choice:"), 2)
    models_md = find_models_md(project.root)
    table = role_table(models_md)
    resolved = {k: table.get(str(v), str(v)) for k, v in models_cfg.items()}
    lock = block.get("gpu_lock")
    lock_path = (project.root / lock).resolve() if lock else None
    if lock_path and not lock_path.is_file():
        raise BookToolsError(f"research.gpu_lock points to a missing file: {lock_path}", 2)
    get = lambda k: block[k] if block.get(k) is not None else DEFAULTS[k]  # noqa: E731  (null -> default)
    return ResearchConfig(
        project=project, endpoint=str(get("endpoint")).rstrip("/"), models=resolved,
        roles={k: str(v) for k, v in models_cfg.items()}, chunk_words=int(get("chunk_words")),
        judge_min_score=int(get("judge_min_score")), k=int(get("k")),
        context_length=int(get("context_length")), citation_style=str(get("citation_style")),
        gpu_lock=lock_path, models_md=models_md, extra=block,
    )
