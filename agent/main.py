"""
Main agent loop.
- Routes through LiteLLM using each user's virtual key (budget enforced per user)
- Traced end-to-end by LangSmith
- Skills loaded from GitHub repo at startup, cached in Redis
- MCP servers started as subprocesses per request
- Auth context injected per connector:
    m365_* → user_token (Graph API, already OBO-exchanged)
    jira_* → user_oid   (Jira server looks up OAuth token from Redis)
    sf_*   → department (Salesforce server picks the right CData connection)
"""

import asyncio
import json
import logging
import subprocess

import anthropic
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic
from pydantic import BaseModel

from agent.tools import ALL_TOOLS
from auth.entra import verify_entra_token
from auth.jira_auth import get_jira_auth_url, handle_jira_oauth_callback
from auth.litellm_provisioner import get_or_create_litellm_key
from auth.obo_flow import exchange_token_for_graph
from config import Config
from skills.loader import invalidate_skill_cache, load_all_skills

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skills — loaded at startup, cached in Redis
# ---------------------------------------------------------------------------

_SKILLS: dict[str, str] = {}


async def init_agent():
    global _SKILLS
    _SKILLS = await load_all_skills()
    logger.info(f"Agent initialised. Skills loaded: {[k for k, v in _SKILLS.items() if v]}")


def _build_system_prompt() -> str:
    skill_sections = "\n\n---\n\n".join(
        f"## {name.upper()} SKILL\n\n{content}"
        for name, content in _SKILLS.items()
        if content
    )
    return f"""You are the Forcepoint Enterprise AI Assistant.
You help all Forcepoint employees with their work.

You have access to three enterprise systems via MCP connectors:
- **Microsoft 365** (m365_*): email, calendar, Teams channels, SharePoint/OneDrive files
- **Jira** (jira_*): issues, sprints, epics, stories, comments, status transitions
- **Salesforce** (sf_*): pipeline, closed won/lost, account health, renewals, ACV by product

## SKILLS
{skill_sections}

## Rules
- Apply field filtering ($select, curated column sets) on every call — never fetch full objects
- Never return more than 20 items unless the user explicitly asks for more
- For Salesforce executive reporting always use ACV_Reporting__c, not ACV__c
- For Jira, default project is AI unless told otherwise
- If a skill says skip discovery — skip it, go straight to the data tool
- Be concise. Employees are busy.
- If unsure of a Jira key or Salesforce account name, search before assuming
"""


# ---------------------------------------------------------------------------
# MCP subprocess management
# ---------------------------------------------------------------------------

def _start_mcp_servers() -> dict[str, subprocess.Popen]:
    procs = {}
    for name, module in [
        ("m365",       "mcp_servers.m365_server"),
        ("jira",       "mcp_servers.jira_server"),
        ("salesforce", "mcp_servers.salesforce_server"),
    ]:
        procs[name] = subprocess.Popen(
            ["python", "-m", module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.debug(f"MCP server started: {name} (pid {procs[name].pid})")
    return procs


def _stop_mcp_servers(procs: dict[str, subprocess.Popen]):
    for name, proc in procs.items():
        proc.terminate()
        logger.debug(f"MCP server terminated: {name}")


async def _call_mcp(tool_name: str, arguments: dict, procs: dict) -> str:
    prefix_map = {"m365_": "m365", "jira_": "jira", "sf_": "salesforce"}
    server_key = next(
        (v for k, v in prefix_map.items() if tool_name.startswith(k)), None
    )
    if not server_key:
        return json.dumps({"error": f"No MCP server for tool: {tool_name}"})

    proc    = procs[server_key]
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method":  "tools/call",
        "params":  {"name": tool_name, "arguments": arguments},
    }) + "\n"

    try:
        proc.stdin.write(request.encode())
        proc.stdin.flush()
        line = await asyncio.get_event_loop().run_in_executor(None, proc.stdout.readline)
        response = json.loads(line)
        content  = response.get("result", {}).get("content", [])
        return content[0].get("text", "{}") if content else "{}"
    except Exception as e:
        logger.error(f"MCP call error [{tool_name}]: {e}")
        return json.dumps({"error": str(e), "tool": tool_name})


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

@traceable(
    name="fp-agent-turn",
    metadata={"version": "1.0", "connector_set": "m365+jira+salesforce"},
)
async def run_agent(
    user_message: str,
    user_id:      str,
    department:   str,
    litellm_key:  str,
    graph_token:  str,
) -> str:
    """
    Run one agent turn.

    Args:
        user_message: The employee's natural language request.
        user_id:      Entra OID — used as Jira user_oid and LangSmith trace key.
        department:   From Entra groups — selects the CData/SF permission tier.
        litellm_key:  User's virtual LiteLLM key — enforces their spend budget.
        graph_token:  Already-exchanged Graph API token — passed to m365_ tools.
    """
    # Per-user client — LiteLLM enforces their virtual key spend limit
    call_client = wrap_anthropic(anthropic.Anthropic(
        base_url=Config.LITELLM_BASE_URL,
        api_key=litellm_key,
    ))

    messages = [{"role": "user", "content": user_message}]
    system   = _build_system_prompt()
    procs    = _start_mcp_servers()

    try:
        while True:
            response = call_client.messages.create(
                model=Config.CLAUDE_MODEL,
                max_tokens=2048,
                system=system,
                tools=ALL_TOOLS,
                messages=messages,
                metadata={"user_id": user_id, "turn_index": len(messages)},
            )

            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                logger.info(
                    f"Turn complete | user={user_id} "
                    f"| in={response.usage.input_tokens} out={response.usage.output_tokens}"
                )
                return final_text

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    args = dict(block.input)

                    if block.name.startswith("m365_"):
                        args["user_token"] = graph_token
                    elif block.name.startswith("jira_"):
                        args["user_oid"] = user_id
                    elif block.name.startswith("sf_"):
                        args["department"] = department

                    logger.debug(f"Tool: {block.name}")
                    result = await _call_mcp(block.name, args, procs)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user",      "content": tool_results})

            else:
                logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                return f"Unexpected stop reason: {response.stop_reason}"

    finally:
        _stop_mcp_servers(procs)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

http_app  = FastAPI(title="Forcepoint Enterprise AI")
_security = HTTPBearer()


class ChatRequest(BaseModel):
    message: str


@http_app.on_event("startup")
async def startup():
    await init_agent()


@http_app.post("/chat")
async def chat(
    request:     ChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_security),
    user:        dict = Depends(verify_entra_token),
):
    user_oid   = user["oid"]
    user_email = user.get("upn") or user.get("preferred_username", "")
    department = _extract_department(user)

    litellm_key = await get_or_create_litellm_key(user_oid, user_email, department)
    graph_token = exchange_token_for_graph(credentials.credentials)

    return {
        "response": await run_agent(
            user_message=request.message,
            user_id=user_oid,
            department=department,
            litellm_key=litellm_key,
            graph_token=graph_token,
        )
    }


@http_app.get("/health")
async def health():
    return {"status": "ok", "skills": {k: bool(v) for k, v in _SKILLS.items()}}


@http_app.post("/skills/invalidate/{skill_name}")
async def invalidate_skill(skill_name: str):
    """Webhook called by the skills pipeline after a new skill version is pushed."""
    await invalidate_skill_cache(skill_name)
    return {"status": "invalidated", "skill": skill_name}


# ---------------------------------------------------------------------------
# Jira OAuth flow
# ---------------------------------------------------------------------------

async def _get_redis() -> aioredis.Redis:
    return await aioredis.from_url(Config.REDIS_URL, decode_responses=True)


@http_app.get("/auth/jira/start")
async def jira_auth_start(user: dict = Depends(verify_entra_token)):
    """Redirect the user here when they first use a Jira feature."""
    return {"auth_url": get_jira_auth_url(user_oid=user["oid"])}


@http_app.get("/auth/jira/callback")
async def jira_auth_callback(code: str, state: str):
    """Atlassian redirects here after the user authorises. state = user_oid."""
    r = await _get_redis()
    await handle_jira_oauth_callback(code=code, user_oid=state, redis_client=r)
    return {"status": "Jira authorisation complete. You can close this tab."}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_department(token_payload: dict) -> str:
    """Map Entra group membership to a department tier for budget + SF access control."""
    groups = token_payload.get("groups", [])
    roles  = token_payload.get("roles", [])

    dept_map = {
        "sales":       Config.ENTRA_GROUP_SALES,
        "engineering": Config.ENTRA_GROUP_ENG,
        "finance":     Config.ENTRA_GROUP_FINANCE,
    }
    for dept, gid in dept_map.items():
        if gid and gid in groups:
            return dept
    for role in roles:
        if role.lower() in dept_map:
            return role.lower()
    return "default"
