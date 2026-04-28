"""
Microsoft 365 MCP Connector
Covers: Email, Calendar, Teams, SharePoint/OneDrive
Auth: Graph API token received from agent (OBO exchange already done in agent/main.py)
RBAC: Enforced entirely by Microsoft Graph API
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from mcp.types import Tool

from .base_server import BaseMCPServer

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class M365Server(BaseMCPServer):

    @property
    def server_name(self) -> str:
        return "m365-mcp-server"

    def _headers(self, user_token: str) -> dict:
        return {
            "Authorization": f"Bearer {user_token}",
            "Content-Type":  "application/json",
        }

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="m365_get_emails",
                description=(
                    "Search the signed-in user's emails. "
                    "Returns preview only — never full body unless explicitly asked."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query":      {"type": "string"},
                        "days_back":  {"type": "integer", "default": 7},
                        "top":        {"type": "integer", "default": 10, "maximum": 20},
                        "folder":     {"type": "string", "default": "inbox",
                                       "enum": ["inbox", "sentitems", "drafts", "archive"]},
                        "user_token": {"type": "string",
                                       "description": "Graph API token — injected by agent"},
                    },
                    "required": ["query", "user_token"],
                },
            ),
            Tool(
                name="m365_get_calendar",
                description="Get the signed-in user's calendar events between two dates.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                        "user_token": {"type": "string"},
                    },
                    "required": ["start_date", "end_date", "user_token"],
                },
            ),
            Tool(
                name="m365_search_files",
                description="Search OneDrive and SharePoint files the user has access to.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query":      {"type": "string"},
                        "top":        {"type": "integer", "default": 10, "maximum": 20},
                        "user_token": {"type": "string"},
                    },
                    "required": ["query", "user_token"],
                },
            ),
            Tool(
                name="m365_get_teams_messages",
                description=(
                    "Get messages from a specific Teams channel. "
                    "Use m365_list_teams first if team_id or channel_id is unknown."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "team_id":    {"type": "string"},
                        "channel_id": {"type": "string"},
                        "top":        {"type": "integer", "default": 20, "maximum": 50},
                        "user_token": {"type": "string"},
                    },
                    "required": ["team_id", "channel_id", "user_token"],
                },
            ),
            Tool(
                name="m365_list_teams",
                description="List all Teams the signed-in user is a member of.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_token": {"type": "string"},
                    },
                    "required": ["user_token"],
                },
            ),
            Tool(
                name="m365_get_profile",
                description="Get the signed-in user's M365 profile.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_token": {"type": "string"},
                    },
                    "required": ["user_token"],
                },
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict):
        user_token = arguments.get("user_token")
        if not user_token:
            return self.err("user_token is required — Graph API token not provided")
        headers = self._headers(user_token)

        async with httpx.AsyncClient(timeout=15.0) as client:

            if name == "m365_get_emails":
                since = (
                    datetime.utcnow() - timedelta(days=arguments.get("days_back", 7))
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                folder = arguments.get("folder", "inbox")
                params = {
                    "$search":  f'"{arguments["query"]}"',
                    "$filter":  f"receivedDateTime ge {since}",
                    "$select":  "id,subject,from,receivedDateTime,bodyPreview,importance,isRead",
                    "$top":     str(min(arguments.get("top", 10), 20)),
                    "$orderby": "receivedDateTime desc",
                }
                r = await client.get(
                    f"{GRAPH_BASE}/me/mailFolders/{folder}/messages",
                    headers=headers, params=params,
                )
                r.raise_for_status()
                return self.ok([
                    {
                        "id":       m["id"],
                        "subject":  m.get("subject"),
                        "from":     m.get("from", {}).get("emailAddress", {}).get("address"),
                        "received": m.get("receivedDateTime"),
                        "preview":  (m.get("bodyPreview") or "")[:200],
                        "isRead":   m.get("isRead"),
                    }
                    for m in r.json().get("value", [])
                ])

            elif name == "m365_get_calendar":
                params = {
                    "startDateTime": f"{arguments['start_date']}T00:00:00Z",
                    "endDateTime":   f"{arguments['end_date']}T23:59:59Z",
                    "$select":       "subject,start,end,attendees,location,bodyPreview,organizer",
                    "$top":          "50",
                    "$orderby":      "start/dateTime asc",
                }
                r = await client.get(
                    f"{GRAPH_BASE}/me/calendarView", headers=headers, params=params,
                )
                r.raise_for_status()
                return self.ok([
                    {
                        "subject":   e.get("subject"),
                        "start":     e.get("start", {}).get("dateTime"),
                        "end":       e.get("end", {}).get("dateTime"),
                        "location":  e.get("location", {}).get("displayName"),
                        "organizer": e.get("organizer", {}).get("emailAddress", {}).get("address"),
                        "attendees": [
                            a.get("emailAddress", {}).get("address")
                            for a in (e.get("attendees") or [])
                        ][:8],
                    }
                    for e in r.json().get("value", [])
                ])

            elif name == "m365_search_files":
                q = arguments["query"]
                r = await client.get(
                    f"{GRAPH_BASE}/me/drive/root/search(q='{q}')",
                    headers=headers,
                    params={
                        "$select": "name,webUrl,lastModifiedDateTime,size",
                        "$top":    str(min(arguments.get("top", 10), 20)),
                    },
                )
                r.raise_for_status()
                return self.ok([
                    {
                        "name":     f.get("name"),
                        "url":      f.get("webUrl"),
                        "modified": f.get("lastModifiedDateTime"),
                        "size_kb":  round((f.get("size") or 0) / 1024, 1),
                    }
                    for f in r.json().get("value", [])
                ])

            elif name == "m365_get_teams_messages":
                tid, cid = arguments["team_id"], arguments["channel_id"]
                r = await client.get(
                    f"{GRAPH_BASE}/teams/{tid}/channels/{cid}/messages",
                    headers=headers,
                    params={
                        "$select": "id,body,from,createdDateTime",
                        "$top":    str(min(arguments.get("top", 20), 50)),
                    },
                )
                r.raise_for_status()
                return self.ok([
                    {
                        "from":    m.get("from", {}).get("user", {}).get("displayName"),
                        "created": m.get("createdDateTime"),
                        "content": (m.get("body", {}).get("content") or "")[:300],
                    }
                    for m in r.json().get("value", [])
                ])

            elif name == "m365_list_teams":
                r = await client.get(
                    f"{GRAPH_BASE}/me/joinedTeams",
                    headers=headers,
                    params={"$select": "id,displayName,description"},
                )
                r.raise_for_status()
                return self.ok(r.json().get("value", []))

            elif name == "m365_get_profile":
                r = await client.get(
                    f"{GRAPH_BASE}/me",
                    headers=headers,
                    params={"$select": "id,displayName,mail,jobTitle,department,officeLocation"},
                )
                r.raise_for_status()
                return self.ok(r.json())

        return self.err(f"Unknown tool: {name}")


if __name__ == "__main__":
    server = M365Server()
    asyncio.run(server.run())
