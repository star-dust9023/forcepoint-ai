"""
Retrieval layer for Ask AI.

Each retriever turns a user question into a ranked list of ``Chunk`` objects
pulled from one internal source. The RAG layer (``rag.py``) merges them, keeps
the best, and hands them to Claude as grounded context.

Scoring is a deliberately simple, dependency-free keyword overlap (TF with a
heading bonus). It is good enough for the MVP corpus and keeps the contained
service free of a vector DB. Swap ``_score`` for embeddings later without
touching the retriever interfaces or the RAG layer.

Retrievers available today:
  - SkillsRetriever       — markdown skills from the GitHub skills repo
  - PortalContentRetriever — local knowledge files (ask_service/knowledge/)

Retrievers wired but dormant until access is granted (return [] + warn once):
  - SharePointRetriever   — gated on ASK_SHAREPOINT_ENABLED
  - ConfluenceRetriever   — gated on ASK_CONFLUENCE_ENABLED
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from ask_service.config import AskConfig

logger = logging.getLogger("ask_service.retrieval")

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "how", "do", "i", "what", "with", "my", "me", "can", "you", "be", "this",
    "that", "it", "as", "at", "by", "from", "about", "use", "using", "get",
}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass
class Chunk:
    """A retrievable unit of grounded text plus where it came from."""
    text: str
    source_title: str
    source_type: str            # "skill" | "portal" | "sharepoint" | "confluence"
    source_url: str = ""
    score: float = 0.0
    extra: dict = field(default_factory=dict)


def _score(query_tokens: list[str], chunk_text: str, heading: str = "") -> float:
    """TF overlap with a bonus for query terms that appear in the heading."""
    if not query_tokens:
        return 0.0
    qset = set(query_tokens)
    body = _tokens(chunk_text)
    if not body:
        return 0.0
    hits = sum(1 for t in body if t in qset)
    coverage = len({t for t in body if t in qset}) / len(qset)  # fraction of query covered
    heading_hits = sum(1 for t in _tokens(heading) if t in qset)
    return hits + heading_hits * 3 + coverage * 5


def _chunk_markdown(md: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) sections on ## / ### headings."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    buf: list[str] = []
    for line in md.splitlines():
        if re.match(r"^#{1,3}\s+", line):
            if buf:
                sections.append((current_heading, "\n".join(buf).strip()))
                buf = []
            current_heading = re.sub(r"^#{1,3}\s+", "", line).strip()
        else:
            buf.append(line)
    if buf:
        sections.append((current_heading, "\n".join(buf).strip()))
    # Keep only sections with real content.
    return [(h, b) for h, b in sections if b]


# ─────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────

class Retriever:
    name: str = "base"
    source_type: str = "base"

    async def retrieve(self, query: str, k: int) -> list[Chunk]:
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


# ─────────────────────────────────────────────────────────────────────────
# Skills (GitHub repo)
# ─────────────────────────────────────────────────────────────────────────

class SkillsRetriever(Retriever):
    """Pulls skill markdown from the internal GitHub skills repo and ranks
    sections against the query. Caches fetched markdown in-process with a TTL
    (no Redis dependency — this service can run on a box without it)."""

    name = "skills"
    source_type = "skill"

    def __init__(self):
        self._cache: dict[str, tuple[float, str]] = {}  # name -> (fetched_at, md)

    async def _fetch(self, client: httpx.AsyncClient, name: str, filename: str) -> str:
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached and now - cached[0] < AskConfig.SKILLS_CACHE_TTL:
            return cached[1]

        url = f"{AskConfig.SKILLS_REPO_BASE_URL}/{filename}"
        headers = {}
        if AskConfig.SKILLS_GITHUB_TOKEN:
            headers["Authorization"] = f"token {AskConfig.SKILLS_GITHUB_TOKEN}"
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            self._cache[name] = (now, resp.text)
            return resp.text
        except Exception as e:
            logger.warning("Skill '%s' unreachable at %s: %s", name, url, e)
            return ""

    async def retrieve(self, query: str, k: int) -> list[Chunk]:
        qt = _tokens(query)
        chunks: list[Chunk] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            fetched = await asyncio.gather(
                *(self._fetch(client, n, f) for n, f in AskConfig.SKILLS.items())
            )
        for (name, _filename), md in zip(AskConfig.SKILLS.items(), fetched):
            if not md:
                continue
            for heading, body in _chunk_markdown(md):
                s = _score(qt, body, heading)
                if s <= 0:
                    continue
                title = f"{name.upper()} skill" + (f" — {heading}" if heading else "")
                chunks.append(Chunk(
                    text=body[:1500],
                    source_title=title,
                    source_type=self.source_type,
                    source_url=f"{AskConfig.SKILLS_REPO_BASE_URL}/{_filename}",
                    score=s,
                ))
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:k]

    async def ready(self) -> bool:
        return True


# ─────────────────────────────────────────────────────────────────────────
# Portal content (local knowledge files)
# ─────────────────────────────────────────────────────────────────────────

class PortalContentRetriever(Retriever):
    """Ranks sections of the local knowledge markdown files that capture the
    portal's own facts (AI policy, getting started, skills catalogue, programs
    and contacts). Fully local — available today, no external access."""

    name = "portal"
    source_type = "portal"

    def __init__(self):
        self._docs: list[tuple[str, str, str]] | None = None  # (file_title, heading, body)

    def _load(self) -> list[tuple[str, str, str]]:
        if self._docs is not None:
            return self._docs
        docs: list[tuple[str, str, str]] = []
        d = AskConfig.KNOWLEDGE_DIR
        if not d.exists():
            logger.warning("Knowledge dir %s missing — portal grounding disabled", d)
            self._docs = []
            return self._docs
        for path in sorted(d.glob("*.md")):
            try:
                md = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Cannot read knowledge file %s: %s", path, e)
                continue
            # First H1 (if any) becomes the file title.
            m = re.search(r"^#\s+(.+)$", md, flags=re.MULTILINE)
            file_title = m.group(1).strip() if m else path.stem.replace("-", " ").title()
            for heading, body in _chunk_markdown(md):
                docs.append((file_title, heading, body))
        self._docs = docs
        logger.info("Portal knowledge loaded: %d sections from %s", len(docs), d)
        return docs

    async def retrieve(self, query: str, k: int) -> list[Chunk]:
        qt = _tokens(query)
        chunks: list[Chunk] = []
        for file_title, heading, body in self._load():
            s = _score(qt, body, f"{file_title} {heading}")
            if s <= 0:
                continue
            title = file_title + (f" — {heading}" if heading and heading != file_title else "")
            chunks.append(Chunk(
                text=body[:1500],
                source_title=title,
                source_type=self.source_type,
                source_url="",
                score=s,
            ))
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:k]


# ─────────────────────────────────────────────────────────────────────────
# SharePoint / Confluence — wired but dormant
# ─────────────────────────────────────────────────────────────────────────

class _DormantRetriever(Retriever):
    """Shared base for sources we cannot reach yet. Returns nothing and warns
    exactly once so logs aren't spammed. When access lands, subclass and
    implement ``retrieve`` (and flip the enable flag)."""

    enabled_flag = False
    _warned = False

    async def retrieve(self, query: str, k: int) -> list[Chunk]:
        if not self.enabled_flag:
            if not self.__class__._warned:
                logger.info(
                    "%s retriever is dormant (access not yet provisioned) — skipping",
                    self.name,
                )
                self.__class__._warned = True
            return []
        return await self._retrieve_live(query, k)

    async def _retrieve_live(self, query: str, k: int) -> list[Chunk]:
        raise NotImplementedError(
            f"{self.name} access is enabled but no live retriever is implemented yet"
        )

    async def ready(self) -> bool:
        return self.enabled_flag


class SharePointRetriever(_DormantRetriever):
    """Primary document RAG source. Dormant until Graph API access is
    provisioned; flip ASK_SHAREPOINT_ENABLED=1 and implement ``_retrieve_live``
    (Graph search → document chunks) to activate."""

    name = "sharepoint"
    source_type = "sharepoint"
    enabled_flag = AskConfig.SHAREPOINT_ENABLED


class ConfluenceRetriever(_DormantRetriever):
    """Secondary RAG source. Dormant until Confluence access is provisioned;
    flip ASK_CONFLUENCE_ENABLED=1 and implement ``_retrieve_live``."""

    name = "confluence"
    source_type = "confluence"
    enabled_flag = AskConfig.CONFLUENCE_ENABLED


# ─────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────

def build_retrievers() -> list[Retriever]:
    return [
        PortalContentRetriever(),
        SkillsRetriever(),
        SharePointRetriever(),
        ConfluenceRetriever(),
    ]
