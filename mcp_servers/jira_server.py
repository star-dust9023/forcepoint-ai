"""
Atlassian Jira MCP Connector
Auth: Per-user OAuth 3LO — token fetched from Redis by user_oid.
      With OAuth, the Jira API base URL is api.atlassian.com/ex/jira/{cloud_id},
      not the tenant URL. RBAC enforced by Jira project permissions.
"""

import asyncio

import httpx
import redis.asyncio as aioredis
from mcp.types import Tool

from auth.jira_auth import get_jira_token, get_user_oauth_headers
from config import Config

from .base_server import BaseMCPServer

LEAN_FIELDS = "summary,status,assignee,priority,issuetype,parent,created,updated,labels"


class JiraServer(BaseMCPServer):

    @property
    def server_name(self) -> str:
        return "jira-mcp-server"

    async def _resolve(self, user_oid: str) -> tuple[dict, str]:
        """
        Returns (headers, jira_base_url) for this user.
        OAuth 3LO uses api.atlassian.com/ex/jira/{cloud_id} — not the tenant URL.
        Raises ValueError if the user has not completed OAuth.
        """
        r = await aioredis.from_url(Config.REDIS_URL, decode_responses=True)
        token_data = await get_jira_token(user_oid, r)
        base = f"https://api.atlassian.com/ex/jira/{token_data['cloud_id']}"
        return get_user_oauth_headers(token_data["access_token"]), base

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="jira_search",
                description=(
                    "Search Jira using JQL. Returns lean payload. "
                    "Use cached JQL patterns from the Jira skill."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "jql":         {"type": "string"},
                        "max_results": {"type": "integer", "default": 20, "maximum": 50},
                        "user_oid":    {"type": "string",
                                        "description": "Entra OID — injected by agent"},
                    },
                    "required": ["jql", "user_oid"],
                },
            ),
            Tool(
                name="jira_get_issue",
                description="Get a specific Jira issue by key, e.g. AI-86.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key":        {"type": "string"},
                        "include_comments": {"type": "boolean", "default": False},
                        "user_oid":         {"type": "string"},
                    },
                    "required": ["issue_key", "user_oid"],
                },
            ),
            Tool(
                name="jira_create_issue",
                description="Create a Jira story, task, or sub-task. Link to epic via parent_key.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary":     {"type": "string"},
                        "description": {"type": "string"},
                        "issue_type":  {"type": "string", "default": "Story",
                                        "enum": ["Story", "Task", "Sub-task", "Epic", "Bug"]},
                        "project_key": {"type": "string", "default": "AI"},
                        "parent_key":  {"type": "string"},
                        "priority":    {"type": "string", "default": "Medium",
                                        "enum": ["Highest", "High", "Medium", "Low"]},
                        "assignee_id": {"type": "string"},
                        "user_oid":    {"type": "string"},
                    },
                    "required": ["summary", "user_oid"],
                },
            ),
            Tool(
                name="jira_add_comment",
                description="Add a comment to a Jira issue.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string"},
                        "comment":   {"type": "string"},
                        "user_oid":  {"type": "string"},
                    },
                    "required": ["issue_key", "comment", "user_oid"],
                },
            ),
            Tool(
                name="jira_update_status",
                description="Move a Jira issue to a new workflow status.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key":       {"type": "string"},
                        "transition_name": {
                            "type": "string",
                            "enum": [
                                "Backlog",
                                "Discovery / Scope",
                                "Selected for Development",
                                "In Progress",
                                "Done",
                            ],
                        },
                        "user_oid": {"type": "string"},
                    },
                    "required": ["issue_key", "transition_name", "user_oid"],
                },
            ),
            Tool(
                name="jira_get_sprint",
                description="Get all issues in a named sprint.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sprint_name": {"type": "string",
                                        "description": "e.g. 'EAI 2026 Sprint 08'"},
                        "project_key": {"type": "string", "default": "AI"},
                        "max_results": {"type": "integer", "default": 50},
                        "user_oid":    {"type": "string"},
                    },
                    "required": ["sprint_name", "user_oid"],
                },
            ),
            Tool(
                name="jira_get_epic_children",
                description="Get all stories and tasks under a specific epic.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "epic_key":    {"type": "string"},
                        "max_results": {"type": "integer", "default": 50},
                        "user_oid":    {"type": "string"},
                    },
                    "required": ["epic_key", "user_oid"],
                },
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict):
        user_oid = arguments.get("user_oid")
        if not user_oid:
            return self.err("user_oid is required for Jira tools")

        headers, base = await self._resolve(user_oid)

        async with httpx.AsyncClient(timeout=15.0) as client:

            if name == "jira_search":
                r = await client.get(
                    f"{base}/rest/api/3/search",
                    headers=headers,
                    params={
                        "jql":        arguments["jql"],
                        "maxResults": min(arguments.get("max_results", 20), 50),
                        "fields":     LEAN_FIELDS,
                    },
                )
                r.raise_for_status()
                return self.ok([self._lean(i) for i in r.json().get("issues", [])])

            elif name == "jira_get_issue":
                fields = LEAN_FIELDS
                if arguments.get("include_comments"):
                    fields += ",comment,description"
                r = await client.get(
                    f"{base}/rest/api/3/issue/{arguments['issue_key']}",
                    headers=headers,
                    params={"fields": fields},
                )
                r.raise_for_status()
                data   = r.json()
                result = self._lean(data)
                if arguments.get("include_comments"):
                    f = data.get("fields", {})
                    result["comments"] = [
                        {
                            "author":  c.get("author", {}).get("displayName"),
                            "body":    self._adf_to_text(c.get("body"))[:300],
                            "created": c.get("created"),
                        }
                        for c in (f.get("comment") or {}).get("comments", [])[-5:]
                    ]
                return self.ok(result)

            elif name == "jira_create_issue":
                body: dict = {
                    "fields": {
                        "project":   {"key": arguments.get("project_key", Config.JIRA_DEFAULT_PROJECT)},
                        "summary":   arguments["summary"],
                        "issuetype": {"name": arguments.get("issue_type", "Story")},
                        "priority":  {"name": arguments.get("priority", "Medium")},
                    }
                }
                if arguments.get("description"):
                    body["fields"]["description"] = self._to_adf(arguments["description"])
                if arguments.get("parent_key"):
                    body["fields"]["parent"] = {"key": arguments["parent_key"]}
                if arguments.get("assignee_id"):
                    body["fields"]["assignee"] = {"id": arguments["assignee_id"]}

                r = await client.post(
                    f"{base}/rest/api/3/issue", headers=headers, json=body,
                )
                r.raise_for_status()
                key = r.json().get("key")
                return self.ok({"key": key, "url": f"https://forcepoint.atlassian.net/browse/{key}"})

            elif name == "jira_add_comment":
                r = await client.post(
                    f"{base}/rest/api/3/issue/{arguments['issue_key']}/comment",
                    headers=headers,
                    json={"body": self._to_adf(arguments["comment"])},
                )
                r.raise_for_status()
                return self.ok({"status": "comment added", "issue": arguments["issue_key"]})

            elif name == "jira_update_status":
                issue_key = arguments["issue_key"]
                target    = arguments["transition_name"].lower()

                r = await client.get(
                    f"{base}/rest/api/3/issue/{issue_key}/transitions", headers=headers,
                )
                r.raise_for_status()
                transitions = r.json().get("transitions", [])
                tid = next(
                    (t["id"] for t in transitions if target in t["name"].lower()), None
                )
                if not tid:
                    return self.err(
                        f"Transition '{target}' not found. "
                        f"Available: {[t['name'] for t in transitions]}"
                    )
                r = await client.post(
                    f"{base}/rest/api/3/issue/{issue_key}/transitions",
                    headers=headers,
                    json={"transition": {"id": tid}},
                )
                r.raise_for_status()
                return self.ok({"status": "transitioned", "issue": issue_key,
                                "to": arguments["transition_name"]})

            elif name == "jira_get_sprint":
                project = arguments.get("project_key", Config.JIRA_DEFAULT_PROJECT)
                r = await client.get(
                    f"{base}/rest/api/3/search",
                    headers=headers,
                    params={
                        "jql":        (
                            f'project = {project} '
                            f'AND sprint = "{arguments["sprint_name"]}" '
                            f'ORDER BY status ASC'
                        ),
                        "maxResults": min(arguments.get("max_results", 50), 50),
                        "fields":     LEAN_FIELDS,
                    },
                )
                r.raise_for_status()
                return self.ok([self._lean(i) for i in r.json().get("issues", [])])

            elif name == "jira_get_epic_children":
                r = await client.get(
                    f"{base}/rest/api/3/search",
                    headers=headers,
                    params={
                        "jql":        (
                            f'project = {Config.JIRA_DEFAULT_PROJECT} '
                            f'AND parent = "{arguments["epic_key"]}"'
                        ),
                        "maxResults": min(arguments.get("max_results", 50), 50),
                        "fields":     LEAN_FIELDS,
                    },
                )
                r.raise_for_status()
                return self.ok([self._lean(i) for i in r.json().get("issues", [])])

        return self.err(f"Unknown tool: {name}")

    def _lean(self, issue: dict) -> dict:
        f = issue.get("fields", {})
        return {
            "key":      issue.get("key"),
            "summary":  f.get("summary"),
            "status":   f.get("status", {}).get("name"),
            "type":     f.get("issuetype", {}).get("name"),
            "priority": f.get("priority", {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "parent":   (f.get("parent") or {}).get("key"),
            "labels":   f.get("labels", []),
            "updated":  f.get("updated"),
        }

    def _to_adf(self, text: str) -> dict:
        return {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        }

    def _adf_to_text(self, adf) -> str:
        if not adf or not isinstance(adf, dict):
            return str(adf or "")
        texts = []
        for block in adf.get("content", []):
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    texts.append(inline.get("text", ""))
        return " ".join(texts)


if __name__ == "__main__":
    server = JiraServer()
    asyncio.run(server.run())
