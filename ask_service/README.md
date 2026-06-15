# Ask AI — contained RAG service

A small, self-contained Retrieval-Augmented Generation service that powers the
**Ask AI** chat page in the Enterprise AI portal. It is deliberately **separate
from the agentic connector server** in `agent/main.py`:

- Text-in / text-out only — no MCP connectors, no LiteLLM, no per-user OBO.
- Calls the **Anthropic API directly** with one org key (contained inside
  Forcepoint; the portal never reaches an external Anthropic chat UI).
- Grounds every answer in **internal sources** and cites them.

## Endpoints

| Endpoint | Used by | Shape |
| --- | --- | --- |
| `POST /ask` | the Ask AI chat page | multi-turn, **streamed** (SSE: `sources`, `delta`, `done`, `error`) |
| `GET /health` | ops | liveness + retriever readiness |

`POST /ask` body: `{ "messages": [{ "role": "user"|"assistant", "content": "…" }], "user": { "email": "…", "name": "…" } }`

## Request flow

```text
Browser (Ask AI chat page)
  └─ POST /api/ask            (portal Node server, Okta-session gated, native SSE proxy)
       └─ POST /ask           (this service, ASK_SERVICE_TOKEN gated, streamed)
            ├─ PortalContentRetriever   (local knowledge/, available today)
            ├─ SkillsRetriever          (GitHub skills repo, available today)
            ├─ SharePointRetriever      (dormant until ASK_SHAREPOINT_ENABLED=1)
            └─ ConfluenceRetriever      (dormant until ASK_CONFLUENCE_ENABLED=1)
                 └─ Anthropic API (direct, streamed) → grounded answer + citations
```

## Run

```bash
# from forcepoint-ai/
python3 -m venv .venv && source .venv/bin/activate
pip install -r ask_service/requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...               # or ASK_ANTHROPIC_API_KEY
export ASK_SERVICE_TOKEN=$(openssl rand -hex 24)  # share with the portal
uvicorn ask_service.main:app --host 127.0.0.1 --port 8100
```

Health check (also reports which retrievers are live):

```bash
curl -s localhost:8100/health | jq
```

Ask (streamed SSE — the portal does this for you):

```bash
curl -sN localhost:8100/ask \
  -H "Authorization: Bearer $ASK_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What can I not share with AI tools?"}],"user":{"email":"me@forcepoint.com"}}'
```

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `ASK_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY` | — | Anthropic key (required) |
| `ASK_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model id |
| `ASK_MAX_TOKENS` | `1024` | Max answer tokens |
| `ASK_SERVICE_TOKEN` | — | Shared secret with the portal proxy. Unset ⇒ unauthenticated (dev only) |
| `ASK_TOP_K` | `6` | Chunks sent to the model |
| `ASK_PER_SOURCE_K` | `4` | Chunks pulled per retriever |
| `ASK_KNOWLEDGE_DIR` | `ask_service/knowledge` | Local portal knowledge markdown |
| `SKILLS_REPO_BASE_URL` | internal repo | GitHub skills repo (shared with the agent) |
| `SKILLS_GITHUB_TOKEN` | — | Token for the skills repo if private |
| `ASK_SHAREPOINT_ENABLED` | `0` | Activate the SharePoint retriever (needs Graph API access) |
| `ASK_CONFLUENCE_ENABLED` | `0` | Activate the Confluence retriever |
| `ASK_ALLOWED_ORIGINS` | — | CORS origins (usually empty; the portal calls server-to-server) |

## Adding a live SharePoint / Confluence source

The retrievers are already wired into the registry and return nothing until
enabled. To activate one, in `retrieval.py` implement `_retrieve_live(query, k)`
on the relevant `_DormantRetriever` subclass (Graph search → `Chunk` list), then
set the matching `ASK_*_ENABLED=1`. No changes needed elsewhere.
