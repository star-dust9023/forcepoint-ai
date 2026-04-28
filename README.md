# Forcepoint Enterprise AI Agent

A production-grade AI assistant that gives every Forcepoint employee natural-language access to Microsoft 365, Jira, and Salesforce — all through a single FastAPI endpoint.

Built on Claude (via LiteLLM), with per-user budget enforcement, end-to-end LangSmith tracing, and an MCP connector architecture that keeps each system's native RBAC intact.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Configuration](#configuration)
5. [Quick Start](#quick-start)
6. [Auth Flows](#auth-flows)
7. [API Reference](#api-reference)
8. [Skills System](#skills-system)
9. [Adding a New Connector](#adding-a-new-connector)
10. [Infrastructure](#infrastructure)

---

## Architecture

```text
Employee (browser / Teams tab)
        │
        │  POST /chat  Bearer: <Entra SSO token>
        ▼
┌───────────────────────────────────────────────────────────┐
│  FastAPI  (agent/main.py)                                 │
│                                                           │
│  verify_entra_token()  →  validates JWT signature         │
│  get_or_create_litellm_key()  →  per-user budget key      │
│  exchange_token_for_graph()  →  OBO → Graph API token     │
│                                                           │
│  run_agent()                                              │
│    └─ Claude via LiteLLM  (per-user virtual key)          │
│         ├─ m365_*  ──►  M365 MCP Server  ──►  Graph API  │
│         ├─ jira_*  ──►  Jira MCP Server  ──►  Atlassian  │
│         └─ sf_*    ──►  SF MCP Server    ──►  CData/SF   │
│                                                           │
│  LangSmith traces every turn (user_id = Entra OID)        │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  Infrastructure (docker-compose)                          │
│  mcp_redis    :6379  — skill cache + OAuth token store    │
│  mcp_litellm  :4000  — Claude proxy, budget enforcement   │
│  mcp_vault    :8200  — secret storage (dev mode)          │
└───────────────────────────────────────────────────────────┘
```

**Auth context injected per connector — automatically, no user action:**

| Connector | Mechanism | RBAC enforced by |
|---|---|---|
| `m365_*` | Entra OBO → Graph API token | Microsoft Graph (user's own data only) |
| `jira_*` | Atlassian OAuth 3LO token (Redis) | Jira project permissions |
| `sf_*` | Department-mapped CData connection | Salesforce profile / permission sets |

---

## Project Structure

```text
forcepoint-ai/
├── agent/
│   ├── main.py          # FastAPI app, agent loop, MCP subprocess routing
│   └── tools.py         # Tool definitions passed to Claude (all 3 connectors)
│
├── auth/
│   ├── entra.py         # Entra ID JWT validation (JWKS, RS256)
│   ├── obo_flow.py      # On-Behalf-Of exchange: Entra token → Graph API token
│   ├── jira_auth.py     # Atlassian OAuth 3LO — authorise, callback, auto-refresh
│   ├── salesforce_auth.py  # Department → CData connection mapping
│   └── litellm_provisioner.py  # Per-user virtual key lifecycle (provision, revoke, spend)
│
├── mcp_servers/
│   ├── base_server.py   # Shared MCP base class (error wrapping, response helpers)
│   ├── m365_server.py   # Email, Calendar, Teams, OneDrive/SharePoint
│   ├── jira_server.py   # Issues, sprints, epics, comments, transitions
│   └── salesforce_server.py  # Pipeline, accounts, renewals, ACV via CData SQL
│
├── skills/
│   └── loader.py        # Fetches skill docs from GitHub at startup, caches in Redis
│
├── config.py            # All env var bindings in one place
├── docker-compose.yml   # Redis, Vault, LiteLLM containers
├── litellm_config.yaml  # Model routing, Redis cache, LangSmith callbacks
├── health_check.py      # Infrastructure readiness check (Redis, Vault, LiteLLM, LangSmith)
├── activate.sh          # Activates venv + loads .env in one step
├── .env.example         # Credential template — copy to .env
└── .gitignore
```

---

## Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.12+ | |
| Docker Engine | 24.x+ | For Redis, Vault, LiteLLM |
| Docker Compose | v2.x | Ships with Docker Desktop |

**Python packages** (install into your venv):
```bash
pip install anthropic fastapi uvicorn httpx msal redis pyjwt \
            langsmith mcp python-dotenv pydantic
```

---

## Configuration

Copy the template and fill in required values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `LITELLM_MASTER_KEY` | Yes | Bearer key for the LiteLLM proxy |
| `LITELLM_BASE_URL` | No | Default: `http://localhost:4000` |
| `CLAUDE_MODEL` | No | Default: `claude-sonnet` |
| `REDIS_URL` | No | Default: `redis://localhost:6379` |
| `LANGCHAIN_API_KEY` | Yes | LangSmith API key |
| `LANGCHAIN_PROJECT` | No | Default: `forcepoint-enterprise-ai` |
| **Azure / M365** | | |
| `AZURE_TENANT_ID` | Yes | Entra tenant ID |
| `AZURE_CLIENT_ID` | Yes | App registration client ID |
| `AZURE_CLIENT_SECRET` | Yes | App registration secret |
| **Atlassian / Jira** | | |
| `ATLASSIAN_OAUTH_CLIENT_ID` | Yes | OAuth 2.0 (3LO) app — developer.atlassian.com |
| `ATLASSIAN_OAUTH_CLIENT_SECRET` | Yes | Same app |
| `ATLASSIAN_OAUTH_REDIRECT_URI` | No | Default: `https://your-agent.fpdev.io/auth/jira/callback` |
| `JIRA_DEFAULT_PROJECT` | No | Default: `AI` |
| **Salesforce via CData** | | |
| `CDATA_BASE_URL` | Yes | CData Connect AI base URL |
| `CDATA_API_KEY` | Yes | CData API key |
| `CDATA_CONNECTION_SALES` | No | Default: `Salesforce_Sales` |
| `CDATA_CONNECTION_OPS` | No | Default: `Salesforce_Ops` |
| `CDATA_CONNECTION_DEFAULT` | No | Default: `Salesforce` |
| **Entra Groups** | | |
| `ENTRA_GROUP_SALES` | No | Azure AD group Object ID → sales tier |
| `ENTRA_GROUP_ENG` | No | Azure AD group Object ID → engineering tier |
| `ENTRA_GROUP_FINANCE` | No | Azure AD group Object ID → finance tier |
| **GitHub Skills** | | |
| `SKILLS_REPO_BASE_URL` | No | Raw base URL to the `claude-skills/docs` folder |
| `SKILLS_GITHUB_TOKEN` | No | PAT for internal GitHub (if repo is private) |
| `SKILLS_CACHE_TTL` | No | Default: `3600` seconds |

---

## Quick Start

### 1. Start infrastructure

```bash
docker compose up -d
docker compose ps   # all three should show "healthy" within ~60s
```

### 2. Activate environment

```bash
source activate.sh
```

Activates the Python venv at `~/.venvs/mcp-platform` and loads `.env` into the shell.

### 3. Verify infrastructure

```bash
python3 health_check.py
```

All four components (LiteLLM, Redis, Vault, LangSmith) must show `✔ PASS` before running the agent.

### 4. Run the agent

```bash
uvicorn agent.main:http_app --reload --port 8000
```

### 5. Send a request

```bash
curl http://localhost:8000/chat \
  -H "Authorization: Bearer <entra-token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "What are my Jira tickets in progress this sprint?"}'
```

---

## Auth Flows

### Microsoft 365 — Entra OBO (automatic)

No employee action needed. On every `/chat` request:
1. The agent validates the Entra Bearer token
2. Calls `exchange_token_for_graph()` (MSAL On-Behalf-Of)
3. Injects the resulting Graph API token into all `m365_*` tool calls
4. Microsoft enforces the user's own permissions — they see only their own email, calendar, and files

### Jira — Atlassian OAuth 3LO (one-time per employee)

Employees authorise once. Token stored in Redis, auto-refreshed.

```text
1. Employee visits GET /auth/jira/start
   → Redirected to Atlassian consent screen

2. Employee approves → Atlassian redirects to GET /auth/jira/callback
   → Tokens stored in Redis under key jira_token:{user_oid}

3. All subsequent jira_* tool calls use the stored token automatically
   → Actions appear under the employee's name in Jira audit logs
```

### Salesforce — Department-mapped CData connections (automatic)

No employee action. The user's Entra group membership (from `ENTRA_GROUP_*` config) maps to a CData connection backed by the appropriate Salesforce profile:

| Department | CData connection | Salesforce profile |
|---|---|---|
| `sales` | `Salesforce_Sales` | Full pipeline access |
| `engineering` / `finance` | `Salesforce_Ops` | Read-only operational view |
| `default` | `Salesforce` | Minimal read access |

---

## API Reference

### `POST /chat`

Main agent endpoint.

**Headers:**

```http
Authorization: Bearer <Entra SSO token>
Content-Type: application/json
```

**Body:**
```json
{ "message": "Show me the EMEA renewal pipeline" }
```

**Response:**
```json
{ "response": "Here are the open EMEA renewals..." }
```

---

### `GET /health`

Returns which skills are loaded.

```json
{
  "status": "ok",
  "skills": { "m365": true, "jira": true, "salesforce": true }
}
```

---

### `POST /skills/invalidate/{skill_name}`

Webhook called by the skills validation pipeline after a new skill version is pushed to GitHub. Forces the next request to re-fetch the skill from the repo — no agent restart needed.

```bash
curl -X POST http://localhost:8000/skills/invalidate/jira
```

---

### `GET /auth/jira/start`

Requires Entra Bearer token. Returns the Atlassian OAuth authorisation URL to redirect the employee to.

---

### `GET /auth/jira/callback`

Atlassian redirects here after the employee approves. Exchanges the auth code for tokens and stores them in Redis.

---

## Skills System

Skills are Markdown documents stored in the internal GitHub repo at:

```text
github.cicd.cloud.fpdev.io/BTS/claude-skills/main/docs/
├── m365-skill.md
├── jira-skill.md
└── salesforce-skill.md
```

At startup, the agent fetches all three and injects them into the system prompt. They cache in Redis for `SKILLS_CACHE_TTL` seconds (default 1 hour).

Skills encode query patterns, field sets, and routing rules — they teach Claude how to use each connector efficiently and avoid redundant discovery calls.

To push an updated skill without restarting the agent, call `POST /skills/invalidate/{skill_name}` after merging the new version.

---

## Adding a New Connector

1. **Create the MCP server** — `mcp_servers/your_server.py`, extending `BaseMCPServer`
2. **Add tool definitions** — append a `YOUR_TOOLS` list to `agent/tools.py` and add it to `ALL_TOOLS`
3. **Register the subprocess** — add an entry to `_start_mcp_servers()` in `agent/main.py`
4. **Inject auth context** — add an `elif block.name.startswith("your_"):` branch in the tool injection loop in `run_agent()`
5. **Add a skill doc** — create `your-skill.md` in the GitHub skills repo and add it to `Config.SKILLS`
6. **Add config vars** — any new credentials go in `config.py` and `.env.example`

---

## Infrastructure

### Containers

| Container | Port | Purpose |
|---|---|---|
| `mcp_redis` | `6379` | Skill cache + Jira/SF OAuth token store |
| `mcp_litellm` | `4000` | Claude proxy — per-user virtual keys, spend limits, response caching |
| `mcp_vault` | `8200` | Secret storage (dev mode — replace with production Vault for prod) |

### LiteLLM virtual keys

Each employee gets a virtual key scoped to their department's monthly budget and allowed models. Provisioned automatically on first login via `auth/litellm_provisioner.py`. If a user exceeds their budget, LiteLLM returns 429 — the agent surfaces a friendly message.

| Department | Monthly budget | Models |
|---|---|---|
| Engineering | $30 | claude-sonnet, claude-haiku |
| Sales | $20 | claude-sonnet, claude-haiku |
| Finance | $15 | claude-haiku |
| Default | $10 | claude-haiku |

### Useful commands

```bash
# Check all containers
docker compose ps

# View LiteLLM logs
docker compose logs litellm --tail 50

# Flush Redis (clears skill cache and OAuth tokens)
docker exec mcp_redis redis-cli FLUSHALL

# Check a user's Jira token is stored
docker exec mcp_redis redis-cli GET "jira_token:<user_oid>"

# Run infrastructure health check
python3 health_check.py
```
