"""
Streaming answer generation for Ask AI.

Multi-turn, grounded, and streamed token-by-token. Same containment and
grounding as the rest of the service (direct Anthropic API, retrieval over
internal sources); the latest user turn drives retrieval and the retrieved
context is injected into the system prompt.

stream_answer() is an async generator of (event, data) tuples:
  ("sources", {sources, grounded})  — emitted first, so the UI can show citations
  ("delta",   {text})               — one per streamed text chunk
  ("done",    {model, grounded})    — end of a successful turn
  ("error",   {message})            — recoverable error (shown in the bubble)
"""

from __future__ import annotations

import logging

import anthropic

from ask_service.config import AskConfig
from ask_service.rag import SYSTEM, retrieve_context

logger = logging.getLogger("ask_service.stream")

# Async client for native streaming. None when no key is configured.
_aclient = (
    anthropic.AsyncAnthropic(api_key=AskConfig.ANTHROPIC_API_KEY)
    if AskConfig.ANTHROPIC_API_KEY
    else None
)

_GROUNDED_SUFFIX = (
    "\n\nInternal context for the user's latest question (cite source numbers "
    "like [1] when you use them):\n\n{context}"
)
_UNGROUNDED_SUFFIX = (
    "\n\nNo internal sources matched the latest question. If you cannot answer "
    "from the conversation so far, say so plainly and point the user to the "
    "Forcepoint AI Policy (FP-IS-AI) or the Enterprise AI team "
    "(ITEnterpriseAIteam@forcepoint.com). Do not invent specifics."
)


def _clean_messages(messages: list[dict]) -> list[dict]:
    """Keep only valid user/assistant turns with non-empty string content, and
    ensure the sequence starts with a user turn (Anthropic requires this)."""
    cleaned = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    return cleaned


async def stream_answer(messages: list[dict], user: dict | None = None):
    """Yield (event, data) tuples for one streamed assistant turn."""
    convo = _clean_messages(messages)
    if not convo:
        yield ("error", {"message": "No user message to answer."})
        return

    last_user = next((m["content"] for m in reversed(convo) if m["role"] == "user"), "")
    context, sources, _ = await retrieve_context(last_user)
    system = SYSTEM + (
        _GROUNDED_SUFFIX.format(context=context) if context else _UNGROUNDED_SUFFIX
    )

    # Citations first so the UI can render them alongside the streamed answer.
    yield ("sources", {"sources": sources, "grounded": bool(context)})

    if _aclient is None:
        yield ("error", {"message": "Ask AI is not configured (no API key)."})
        return

    uid = (user or {}).get("email", "anonymous")
    try:
        async with _aclient.messages.stream(
            model=AskConfig.ANTHROPIC_MODEL,
            max_tokens=AskConfig.MAX_TOKENS,
            system=system,
            messages=convo,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield ("delta", {"text": text})
            final = await stream.get_final_message()
        logger.info(
            "Answered | user=%s | turns=%d | sources=%d | in=%s out=%s",
            uid, len(convo), len(sources),
            final.usage.input_tokens, final.usage.output_tokens,
        )
        yield ("done", {"model": AskConfig.ANTHROPIC_MODEL, "grounded": bool(context)})
    except Exception as e:  # noqa: BLE001 — surface a clean message, log detail
        logger.exception("Stream failed | user=%s", uid)
        yield ("error", {"message": f"Ask AI error: {e}"})
