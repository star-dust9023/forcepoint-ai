"""
Retrieval and grounding for Ask AI.

retrieve_context() runs every retriever concurrently, merges and ranks the
chunks, and builds a numbered context block plus the matching source list. The
streaming layer feeds that context to the model, which answers using ONLY the
context — citing sources by number and admitting when the context is
insufficient. SYSTEM is the shared system prompt.
"""

from __future__ import annotations

import asyncio
import logging

from ask_service.config import AskConfig
from ask_service.retrieval import Chunk, build_retrievers

logger = logging.getLogger("ask_service.rag")

_RETRIEVERS = build_retrievers()

SYSTEM = """You are the Forcepoint Intelligence Platform assistant ("Ask AI").

You help Forcepoint employees by answering questions grounded ONLY in the
internal context provided to you. You embody the Forcepoint brand voice: the
Sage archetype — warm, direct, collaborative and radically simple.

Rules:
- Answer using ONLY the numbered context below. Do not use outside knowledge.
- Cite the sources you used inline with their numbers, e.g. [1], [2].
- If the context does not contain the answer, say so plainly and point the user
  to the Enterprise AI team (ITEnterpriseAIteam@forcepoint.com) or the relevant
  resource — do NOT guess or invent details.
- Keep responses to 2–4 sentences unless the user explicitly asks for more.
- Never reproduce or infer Protected Information, source code, customer PII or
  Forcepoint Confidential data.
- Use plain language."""


def _dedupe_and_rank(all_chunks: list[Chunk], top_k: int) -> list[Chunk]:
    """Drop near-duplicate chunks (same title+text prefix), keep the top_k by score."""
    seen: set[tuple[str, str]] = set()
    unique: list[Chunk] = []
    for c in sorted(all_chunks, key=lambda c: c.score, reverse=True):
        key = (c.source_title, c.text[:120])
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique[:top_k]


def _build_context(chunks: list[Chunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, 1):
        loc = f" ({c.source_url})" if c.source_url else ""
        blocks.append(f"[{i}] {c.source_title} — {c.source_type}{loc}\n{c.text}")
    return "\n\n".join(blocks)


async def _gather_chunks(question: str) -> list[Chunk]:
    results = await asyncio.gather(
        *(r.retrieve(question, AskConfig.PER_SOURCE_K) for r in _RETRIEVERS),
        return_exceptions=True,
    )
    chunks: list[Chunk] = []
    for r, res in zip(_RETRIEVERS, results):
        if isinstance(res, Exception):
            logger.warning("Retriever '%s' failed: %s", r.name, res)
            continue
        chunks.extend(res)
    return chunks


async def retrieve_context(question: str) -> tuple[str, list[dict], list[Chunk]]:
    """Run retrieval for a question. Returns (context_block, sources, chunks).

    context_block is "" when nothing matched. sources is the citation list the
    UI renders; chunks carries the underlying retrieved units.
    """
    chunks = _dedupe_and_rank(await _gather_chunks(question), AskConfig.TOP_K)
    context = _build_context(chunks) if chunks else ""
    sources = [
        {"n": i, "title": c.source_title, "type": c.source_type, "url": c.source_url}
        for i, c in enumerate(chunks, 1)
    ]
    return context, sources, chunks


async def retriever_status() -> dict:
    out = {}
    for r in _RETRIEVERS:
        try:
            out[r.name] = await r.ready()
        except Exception:
            out[r.name] = False
    return out
