"""MCP server setup."""
import asyncio
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server

from voog_mcp.config import load_config
from voog_mcp.client import VoogClient
from voog_mcp.errors import error_response
from voog_mcp.tools import layouts as layouts_tools
from voog_mcp.tools import layouts_sync as layouts_sync_tools
from voog_mcp.tools import pages as pages_tools
from voog_mcp.tools import pages_mutate as pages_mutate_tools
from voog_mcp.tools import products as products_tools
from voog_mcp.tools import products_images as products_images_tools
from voog_mcp.tools import redirects as redirects_tools
from voog_mcp.tools import snapshot as snapshot_tools

from voog_mcp.resources import articles as articles_resources
from voog_mcp.resources import layouts as layouts_resources
from voog_mcp.resources import pages as pages_resources
from voog_mcp.resources import products as products_resources
from voog_mcp.resources import redirects as redirects_resources

logger = logging.getLogger("voog-mcp")

# Tool group registry. Each module exports get_tools() and a sync call_tool();
# handle_call_tool dispatches via asyncio.to_thread so blocking urllib I/O
# doesn't stall the event loop. Append new groups here.
TOOL_GROUPS = [
    layouts_tools,
    layouts_sync_tools,
    pages_tools,
    pages_mutate_tools,
    products_tools,
    products_images_tools,
    redirects_tools,
    snapshot_tools,
]

# Resource group registry. Each module exports get_resources(),
# get_uri_patterns(), matches(uri), and a sync read_resource(uri, client) —
# the latter dispatched via asyncio.to_thread, same rationale as tools.
RESOURCE_GROUPS = [
    articles_resources,
    layouts_resources,
    pages_resources,
    products_resources,
    redirects_resources,
]


def _validate_resource_uri_patterns(groups) -> None:
    """Fail-fast on duplicate or overlapping resource URI patterns.

    Each group's :func:`get_uri_patterns` returns the URI / URI_PREFIX
    strings it claims. Two groups conflict if either:

    1. They claim the same pattern verbatim (e.g. both return
       ``"voog://pages"``), or
    2. One pattern is a strict sub-path of another (e.g. ``"voog://pages"``
       and ``"voog://pages/special"``). Under the convention that
       :func:`matches` resolves true for ``uri == pattern`` or
       ``uri.startswith(pattern + "/")``, both groups would match a URI
       like ``voog://pages/special/foo``, and the first-match dispatcher
       in :func:`handle_read_resource` would silently route to whichever
       registered first.

    ``"voog://pagesx"`` vs ``"voog://pages"`` is *not* a collision: the
    trailing-``/`` boundary means ``matches()`` rejects cross-prefix URIs.

    Mirrors the inline tool name collision check below — same fail-fast
    contract, just for resources.
    """
    claims: list[tuple[str, str]] = []  # (pattern, group_name)
    for group in groups:
        group_name = getattr(group, "__name__", repr(group))
        for pattern in group.get_uri_patterns():
            for existing_pattern, existing_group in claims:
                if pattern == existing_pattern:
                    raise RuntimeError(
                        f"Resource URI collision: pattern '{pattern}' claimed "
                        f"by both {existing_group} and {group_name}"
                    )
                if (
                    pattern.startswith(existing_pattern + "/")
                    or existing_pattern.startswith(pattern + "/")
                ):
                    raise RuntimeError(
                        f"Resource URI prefix overlap: '{pattern}' "
                        f"(from {group_name}) overlaps with '{existing_pattern}' "
                        f"(from {existing_group})"
                    )
            claims.append((pattern, group_name))


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

    _validate_resource_uri_patterns(RESOURCE_GROUPS)

    @server.list_tools()
    async def handle_list_tools():
        return [tool for group in TOOL_GROUPS for tool in group.get_tools()]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None):
        group = tool_dispatch.get(name)
        if group is None:
            return error_response(f"Unknown tool: {name}")
        # group.call_tool is sync (does blocking urllib I/O); offload to a worker
        # thread so the MCP event loop stays responsive — notifications can flow,
        # shutdown signals get processed even during a 30–60s site_snapshot run.
        return await asyncio.to_thread(group.call_tool, name, arguments or {}, client)

    @server.list_resources()
    async def handle_list_resources():
        return [r for group in RESOURCE_GROUPS for r in group.get_resources()]

    @server.read_resource()
    async def handle_read_resource(uri):
        uri_str = str(uri)
        for group in RESOURCE_GROUPS:
            if group.matches(uri_str):
                return await asyncio.to_thread(group.read_resource, uri_str, client)
        raise ValueError(f"Unknown resource URI: {uri_str}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
