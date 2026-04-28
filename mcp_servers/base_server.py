"""
Shared base class for all MCP servers.
Handles skill injection, error wrapping, and structured logging.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


class BaseMCPServer(ABC):
    """
    All connectors inherit from this.
    Subclasses implement: server_name, get_tools(), handle_tool()
    """

    def __init__(self):
        self.app = Server(self.server_name)
        self._register_handlers()

    @property
    @abstractmethod
    def server_name(self) -> str:
        pass

    @abstractmethod
    async def get_tools(self) -> list[Tool]:
        pass

    @abstractmethod
    async def handle_tool(self, name: str, arguments: dict) -> list[TextContent]:
        pass

    def _register_handlers(self):
        @self.app.list_tools()
        async def list_tools():
            return await self.get_tools()

        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict):
            try:
                logger.info(f"[{self.server_name}] Tool called: {name}")
                return await self.handle_tool(name, arguments)
            except Exception as e:
                logger.error(f"[{self.server_name}] Tool error [{name}]: {e}")
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": str(e), "tool": name})
                )]

    def ok(self, data) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(data, default=str))]

    def err(self, message: str) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps({"error": message}))]

    async def run(self):
        async with stdio_server() as (read, write):
            await self.app.run(read, write, self.app.create_initialization_options())
