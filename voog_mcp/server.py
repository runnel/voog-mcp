"""MCP server setup."""
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server

from voog_mcp.config import load_config
from voog_mcp.client import VoogClient
from voog_mcp.errors import error_response
from voog_mcp.tools import pages as pages_tools
# Tasks 10-14: append more `from voog_mcp.tools import <group> as <group>_tools` imports below

logger = logging.getLogger("voog-mcp")

# Tool group registry — Tasks 10-14 should append their module here.
# Each module must export get_tools() -> list[Tool] and async call_tool(name, arguments, client) -> list[TextContent].
TOOL_GROUPS = [
    pages_tools,
    # Tasks 10-14: append more tool group modules here
]


async def run_server():
    config = load_config()
    client = VoogClient(host=config.host, api_token=config.api_token)
    server = Server(name="voog-mcp", version="0.1.0")

    # Build (tool name → group module) lookup once at startup.
    # Detects collisions: if two groups define the same tool name, this raises.
    tool_dispatch: dict = {}
    for group in TOOL_GROUPS:
        for tool in group.get_tools():
            if tool.name in tool_dispatch:
                raise RuntimeError(
                    f"Tool name collision: '{tool.name}' defined in multiple tool groups"
                )
            tool_dispatch[tool.name] = group

    @server.list_tools()
    async def handle_list_tools():
        return [tool for group in TOOL_GROUPS for tool in group.get_tools()]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None):
        group = tool_dispatch.get(name)
        if group is None:
            return error_response(f"Unknown tool: {name}")
        return await group.call_tool(name, arguments or {}, client)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
