"""MCP server setup."""
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server

from voog_mcp.config import load_config
from voog_mcp.client import VoogClient

logger = logging.getLogger("voog-mcp")


async def run_server():
    config = load_config()
    # Client will be used by tools/resources registered in Phase C/D.
    client = VoogClient(host=config.host, api_token=config.api_token)  # noqa: F841

    server = Server(name="voog-mcp", version="0.1.0")

    # Tools and resources will be registered in Phase C/D.

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
