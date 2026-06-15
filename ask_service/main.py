"""
Ask AI — standalone FastAPI app, separate from the agentic connector server.

Endpoints:
  POST /ask     — grounded, multi-turn, STREAMED answer over Server-Sent Events.
                  Service-token gated. Body: {messages:[{role,content}], user?}.
                  SSE events: `sources`, `delta`, `done`, `error`.
  GET  /health  — liveness + per-retriever readiness + config sanity.

Run:
  uvicorn ask_service.main:app --host 127.0.0.1 --port 8100
"""

import json
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ask_service.config import AskConfig
from ask_service.rag import retriever_status
from ask_service.stream import stream_answer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ask_service")

app = FastAPI(title="Forcepoint Ask AI", version="1.0")

if AskConfig.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=AskConfig.ALLOWED_ORIGINS,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


class AskUser(BaseModel):
    email: str | None = None
    name: str | None = None


class AskMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=8000)


class AskRequest(BaseModel):
    messages: list[AskMessage] = Field(..., min_length=1, max_length=40)
    user: AskUser | None = None


def require_service_token(authorization: str | None = Header(default=None)):
    """Validate the shared secret the portal proxy sends. Disabled (with a
    startup warning) when ASK_SERVICE_TOKEN is unset — dev convenience only."""
    if not AskConfig.SERVICE_TOKEN:
        return
    expected = f"Bearer {AskConfig.SERVICE_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing service token")


@app.on_event("startup")
async def _startup():
    if not AskConfig.ANTHROPIC_API_KEY:
        logger.warning("⚠  ANTHROPIC_API_KEY is not set — /ask will return 503.")
    if not AskConfig.SERVICE_TOKEN:
        logger.warning("⚠  ASK_SERVICE_TOKEN is not set — /ask is UNAUTHENTICATED (dev only).")
    logger.info("Ask AI ready | model=%s | knowledge=%s",
                AskConfig.ANTHROPIC_MODEL, AskConfig.KNOWLEDGE_DIR)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask", dependencies=[Depends(require_service_token)])
async def ask(req: AskRequest):
    """Streamed, multi-turn grounded answer over Server-Sent Events."""
    if not AskConfig.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Ask AI is not configured (no API key)")

    messages = [m.model_dump() for m in req.messages]
    user = req.user.model_dump() if req.user else None

    async def event_stream():
        async for event, data in stream_answer(messages, user):
            yield _sse(event, data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": AskConfig.ANTHROPIC_MODEL,
        "api_key_configured": bool(AskConfig.ANTHROPIC_API_KEY),
        "service_token_required": bool(AskConfig.SERVICE_TOKEN),
        "retrievers": await retriever_status(),
    }
