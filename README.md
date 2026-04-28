# AI-78: MCP Platform Health Check

Verifies that all four MCP platform infrastructure components — **LiteLLM**, **Redis**, **Vault**, and **LangSmith** — are operational before Sprint 1 begins. All four must pass the acceptance criteria defined in ADD Section 4.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Repository Structure](#repository-structure)
4. [Quick Start](#quick-start)
5. [Component Reference](#component-reference)
   - [LiteLLM](#litellm)
   - [Redis](#redis)
   - [Vault](#vault)
   - [LangSmith](#langsmith)
6. [Configuration Reference](#configuration-reference)
7. [Running the Health Check](#running-the-health-check)
8. [Acceptance Criteria](#acceptance-criteria)
9. [Troubleshooting](#troubleshooting)
10. [Completion Checklist](#completion-checklist)

---

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│  Client / health_check.py                                        │
│       │                                                          │
│       ▼  :4000                                                   │
│  ┌─────────────┐    cache hits    ┌───────────────┐             │
│  │   LiteLLM   │ ◄─────────────► │  Redis :6379  │             │
│  │   (proxy)   │   TTL = 3600s   │  (cache layer)│             │
│  └──────┬──────┘                 └───────────────┘             │
│         │ route                                                   │
│         ▼                         ┌───────────────┐             │
│  Anthropic API                    │  Vault :8200  │             │
│  (claude-sonnet-4-5)              │  (secrets)    │             │
│         │                         └───────────────┘             │
│         │ callbacks                                               │
│         ▼                                                         │
│  LangSmith (cloud)                                               │
│  (traces / observability)                                        │
└──────────────────────────────────────────────────────────────────┘
```

**Data flows:**

- Completion requests hit LiteLLM on `:4000`, which routes them to Anthropic.
- LiteLLM caches responses in Redis with a 3600-second TTL.
- Every invocation emits a trace to LangSmith via the success/failure callbacks.
- Connector credentials (JWT/OAuth2) are stored in Vault and read at runtime.

---

## Prerequisites

### Runtime

| Dependency     | Minimum version | Notes                                                   |
| -------------- | --------------- | ------------------------------------------------------- |
| Docker Engine  | 24.x            | Required for all three containers                       |
| Docker Compose | v2.x            | Ships with Docker Desktop; `docker compose` (no hyphen) |
| Python         | 3.12            | Already on the host                                     |

### API Keys (external services)

| Service            | Environment variable | Where to obtain                                                   |
| ------------------ | -------------------- | ----------------------------------------------------------------- |
| Anthropic          | `ANTHROPIC_API_KEY`  | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| LangSmith          | `LANGCHAIN_API_KEY`  | smith.langchain.com → Settings → API Keys                         |
| LiteLLM master key | `LITELLM_MASTER_KEY` | Any string you generate (e.g. `sk-litellm-local-dev`)             |

Vault and Redis run in development mode and require no external accounts.

---

## Repository Structure

```text
AI-78/
├── .env.example          # Credential template — copy to .env and fill in values
├── .env                  # Your local credentials (git-ignored, never commit)
├── activate.sh           # Activates venv + loads .env in one step
├── docker-compose.yml    # Defines mcp_redis, mcp_vault, mcp_litellm containers
├── litellm_config.yaml   # LiteLLM model routing, Redis cache config, LangSmith callbacks
├── health_check.py       # Automated health check script — tests all four components
└── README.md             # This file
```

> **Note:** The Python virtual environment lives at `~/.venvs/mcp-platform` (shared, outside this repo). It is not committed.

---

## Quick Start

### 1. Clone credentials template

```bash
cp .env.example .env
```

Open `.env` and fill in the three required values:

```dotenv
ANTHROPIC_API_KEY=sk-ant-<your key>
LITELLM_MASTER_KEY=sk-litellm-local-dev
LANGCHAIN_API_KEY=ls__<your key>
```

Everything else (Redis host, Vault token, tracing flags) is pre-configured for local dev.

### 2. Activate the environment

```bash
source activate.sh
```

This activates the Python virtual environment and loads your `.env` into the shell. Run this once per terminal session.

### 3. Pull and start all containers

```bash
docker compose up -d
```

Expected startup order: `mcp_redis` → `mcp_vault` → `mcp_litellm` (LiteLLM waits for Redis to be healthy before starting).

Check that all three are running:

```bash
docker compose ps
```

All three should show `healthy` within ~60 seconds.

### 4. Run the health check

```bash
python3 health_check.py
```

A passing run prints `✔ PASS` for all four components and exits with code `0`.

---

## Component Reference

### LiteLLM

| Property          | Value                                 |
| ----------------- | ------------------------------------- |
| Container         | `mcp_litellm`                         |
| Port              | `4000`                                |
| Image             | `ghcr.io/berriai/litellm:main-latest` |
| Config file       | `litellm_config.yaml`                 |
| Backend model     | `anthropic/claude-sonnet-4-5`         |
| Proxy model alias | `claude-sonnet-4-20250514`            |

**Manual test:**

```bash
curl http://localhost:4000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 10
  }'
```

**Pass:** HTTP 200, response contains a `choices` array with a non-empty message content.

---

### Redis

| Property    | Value                              |
| ----------- | ---------------------------------- |
| Container   | `mcp_redis`                        |
| Port        | `6379`                             |
| Image       | `redis:7-alpine`                   |
| Cache TTL   | `3600` seconds (per ADD Section 4) |
| Persistence | Disabled (in-memory only for dev)  |

**Manual test:**

```bash
# Using redis-py (activated venv)
python3 - <<'EOF'
import redis, os
r = redis.Redis(host="localhost", port=6379)
r.set("mcp_health_check", "ok", ex=3600)
print("GET:", r.get("mcp_health_check"))
print("TTL:", r.ttl("mcp_health_check"))
r.delete("mcp_health_check")
EOF

# Or using docker exec (no redis-cli install needed)
docker exec mcp_redis redis-cli SET mcp_health_check ok EX 3600
docker exec mcp_redis redis-cli GET mcp_health_check
docker exec mcp_redis redis-cli TTL mcp_health_check
```

**Pass:** `GET` returns `ok`. `TTL` returns a value between `3595` and `3600`.

---

### Vault

| Property       | Value                                        |
| -------------- | -------------------------------------------- |
| Container      | `mcp_vault`                                  |
| Port           | `8200`                                       |
| Image          | `hashicorp/vault:1.17`                       |
| Mode           | Dev (auto-unsealed, no snapshot persistence) |
| Root token     | `root` (set via `VAULT_DEV_ROOT_TOKEN_ID`)   |
| Secrets engine | KV v2 at `secret/`                           |

**Manual test:**

```bash
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root

vault status
# Expect: Sealed = false, Initialized = true

vault kv put secret/forcepoint/salesforce \
  client_id="test-client" \
  client_secret="test-secret"

vault kv get secret/forcepoint/salesforce
```

**Pass:** `vault status` shows `Sealed: false`. The `kv get` command returns field names without error.

> **Important:** Dev mode does not persist secrets across container restarts. For production, replace with a properly initialized and unsealed Vault instance backed by a storage backend.

---

### LangSmith

| Property        | Value                                           |
| --------------- | ----------------------------------------------- |
| Service         | Cloud (smith.langchain.com)                     |
| Project         | `forcepoint-eai`                                |
| Tracing trigger | LiteLLM `success_callback` + `failure_callback` |
| Env var         | `LANGCHAIN_TRACING_V2=true`                     |

**Manual test:**

```bash
python3 - <<'EOF'
import os, litellm

litellm.success_callback = ["langsmith"]
litellm.failure_callback = ["langsmith"]

resp = litellm.completion(
    model="openai/claude-sonnet-4-20250514",
    api_base="http://localhost:4000",
    api_key=os.environ["LITELLM_MASTER_KEY"],
    messages=[{"role": "user", "content": "health check trace test"}],
    max_tokens=10,
)
print(resp.choices[0].message.content)
EOF
```

Then open [smith.langchain.com](https://smith.langchain.com) → project `forcepoint-eai` and confirm the trace appears within 30 seconds.

**Pass:** Trace visible in the LangSmith dashboard showing model, input, output, latency, and token count with no errors.

---

## Configuration Reference

All configuration is driven by `.env`. The `litellm_config.yaml` and `docker-compose.yml` reference these variables via `os.environ/` and `${}` substitution respectively.

| Variable               | Required | Default                 | Description                                      |
| ---------------------- | -------- | ----------------------- | ------------------------------------------------ |
| `ANTHROPIC_API_KEY`    | Yes      | —                       | Anthropic API key for Claude routing             |
| `LITELLM_MASTER_KEY`   | Yes      | —                       | Bearer token clients send to the LiteLLM proxy   |
| `LANGCHAIN_API_KEY`    | Yes      | —                       | LangSmith API key for trace ingestion            |
| `LANGCHAIN_TRACING_V2` | Yes      | `true`                  | Enables LangChain/LiteLLM → LangSmith tracing    |
| `LANGCHAIN_PROJECT`    | Yes      | `forcepoint-eai`        | LangSmith project name                           |
| `REDIS_HOST`           | No       | `redis`                 | Redis hostname (use `localhost` outside Docker)  |
| `REDIS_PORT`           | No       | `6379`                  | Redis port                                       |
| `REDIS_PASSWORD`       | No       | —                       | Redis auth password (uncomment if needed)        |
| `VAULT_ADDR`           | No       | `http://localhost:8200` | Vault API address                                |
| `VAULT_TOKEN`          | No       | `root`                  | Vault authentication token                       |
| `VAULT_DEV_ROOT_TOKEN` | No       | `root`                  | Root token injected into the Vault dev container |

---

## Running the Health Check

```bash
# Full automated check — all four components
python3 health_check.py

# Expected output on full pass:
# ────────────────────────────────────────────────────────────
#   Component 1 — LiteLLM
# ────────────────────────────────────────────────────────────
#   ✔ PASS  /health reachable
#   ✔ PASS  Completion routes successfully
#
# ────────────────────────────────────────────────────────────
#   Component 2 — Redis
# ────────────────────────────────────────────────────────────
#   ✔ PASS  Redis reachable (PING)
#   ✔ PASS  SET/GET round-trip
#   ✔ PASS  TTL ≈ 3600s
#
# ────────────────────────────────────────────────────────────
#   Component 3 — Vault
# ────────────────────────────────────────────────────────────
#   ✔ PASS  Vault reachable
#   ✔ PASS  Vault initialized
#   ✔ PASS  Vault unsealed
#   ✔ PASS  Write test secret (kv-v2)
#   ✔ PASS  Read secret fields back
#
# ────────────────────────────────────────────────────────────
#   Component 4 — LangSmith
# ────────────────────────────────────────────────────────────
#   ✔ PASS  LANGCHAIN_API_KEY set
#   ✔ PASS  LANGCHAIN_TRACING_V2=true
#   ✔ PASS  LANGCHAIN_PROJECT set
#   ✔ PASS  LiteLLM call succeeded
```

The script exits `0` on full pass and `1` if any component fails.

---

## Acceptance Criteria

Per AI-78 / ADD Section 4 — all four must be green before Sprint 1 begins:

| #   | Criterion                                                          | Verified by                                            |
| --- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| 1   | LiteLLM routes a test completion with valid response and no errors | `health_check.py` Component 1                          |
| 2   | Redis responds to SET/GET with TTL confirmed at 3600s              | `health_check.py` Component 2                          |
| 3   | Vault is unsealed and a secret can be successfully read            | `health_check.py` Component 3                          |
| 4   | LangSmith trace visible for a test invocation                      | `health_check.py` Component 4 + manual dashboard check |
| 5   | Screenshot evidence archived and attached to AI-66                 | Manual — capture terminal output + LangSmith dashboard |

---

## Troubleshooting

### LiteLLM — `Connection refused` on `:4000`

```bash
docker compose ps          # check mcp_litellm status
docker compose logs litellm --tail 50
```

LiteLLM starts only after Redis is healthy. If Redis is still starting, wait 30 seconds and retry.

### LiteLLM — `401 Unauthorized`

Your `LITELLM_MASTER_KEY` in `.env` must match the `Authorization: Bearer` value in the request. Confirm both are identical.

### LiteLLM — `Model not found`

The request must use `claude-sonnet-4-20250514` exactly — this is the `model_name` alias defined in `litellm_config.yaml`. Any other string will return 404.

### Redis — `TTL` returns `-1`

The key was written without an expiry. This means LiteLLM's `cache_params.ttl` is not being applied. Check that `litellm_config.yaml` is mounted correctly and the `cache: true` block is present.

```bash
docker exec mcp_litellm cat /app/config.yaml
```

### Redis — `TTL` returns `-2`

The key does not exist. You may be connected to the wrong Redis instance. Confirm `REDIS_HOST=redis` inside the LiteLLM container and `localhost` when running commands directly on the host.

### Vault — `Sealed: true`

The dev-mode container auto-unseals on start. If you see `Sealed: true`, the container likely restarted and lost its in-memory state.

```bash
docker compose restart vault
# wait ~5 seconds, then re-run
vault status
```

If running against a production Vault instance (not dev mode), you must unseal with the unseal keys — this is a hard blocker and must be escalated before Sprint 1.

### Vault — `403 Permission Denied`

Your `VAULT_TOKEN` does not have a policy covering the secret path. Check policies:

```bash
vault token lookup
vault policy list
vault policy read default
```

### LangSmith — trace does not appear

1. Confirm `LANGCHAIN_TRACING_V2=true` (the string `"true"`, not `True` or `1`).
2. Confirm `LANGCHAIN_API_KEY` is valid — regenerate at smith.langchain.com → Settings → API Keys if in doubt.
3. Confirm the project name matches exactly: `forcepoint-eai`.
4. Check LiteLLM logs for callback errors: `docker compose logs litellm --tail 100 | grep -i langsmith`.

### Containers won't start — port already in use

```bash
# find what's holding the port
ss -tlnp | grep -E '4000|6379|8200'
```

Stop the conflicting process or change the host-side port mapping in `docker-compose.yml`.

---

## Completion Checklist

Once `health_check.py` reports all four components as `✔ PASS`:

- [ ] Capture terminal screenshot of the full `health_check.py` output
- [ ] Capture screenshot of the LangSmith dashboard showing the trace
- [ ] Attach both screenshots to AI-66 (Snapshot Link field)
- [ ] Note any non-default configuration (non-standard ports, alternate auth method) in the AI-78 comments
- [ ] Set AI-78 fields: **Unit Test Complete → Yes**, **Environment to Test → MCP platform**
- [ ] Transition AI-78 to **Done**
- [ ] Proceed to remaining AI-66 sub-tasks in parallel
