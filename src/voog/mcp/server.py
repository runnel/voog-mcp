"""MCP server entry point. Hosts tools/resources for one or more Voog sites.

Each tool takes a ``site: str`` parameter (except ``voog_list_sites``).
The server resolves it to a SiteConfig from the global config, then
constructs a per-call VoogClient via ``client_factory``. Clients are
cached by site name for the lifetime of the server.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server

from voog.client import VoogClient
from voog.config import (
    ConfigError,
    GlobalConfig,
    default_global_config_path,
    find_env_file,
    load_env_file,
    load_global_config,
)
from voog.errors import error_response
from voog.mcp.resources import articles as articles_resources
from voog.mcp.resources import layouts as layouts_resources
from voog.mcp.resources import pages as pages_resources
from voog.mcp.resources import products as products_resources
from voog.mcp.resources import redirects as redirects_resources
from voog.mcp.tools import layouts as layouts_tools
from voog.mcp.tools import layouts_sync as layouts_sync_tools
from voog.mcp.tools import pages as pages_tools
from voog.mcp.tools import pages_mutate as pages_mutate_tools
from voog.mcp.tools import products as products_tools
from voog.mcp.tools import products_images as products_images_tools
from voog.mcp.tools import redirects as redirects_tools
from voog.mcp.tools import snapshot as snapshot_tools

logger = logging.getLogger("voog")

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

RESOURCE_GROUPS = [
    articles_resources,
    layouts_resources,
    pages_resources,
    products_resources,
    redirects_resources,
]


class ClientFactory:
    """Constructs and caches VoogClient instances per site."""

    def __init__(self, global_cfg: GlobalConfig, env: dict[str, str]):
        self._global_cfg = global_cfg
        self._env = env
        self._cache: dict[str, VoogClient] = {}

    def for_site(self, site_name: str) -> VoogClient:
        if site_name in self._cache:
            return self._cache[site_name]
        if site_name not in self._global_cfg.sites:
            raise ConfigError(
                f"unknown site '{site_name}'. Available: {sorted(self._global_cfg.sites)}"
            )
        site = self._global_cfg.sites[site_name]
        token = self._env.get(site.api_key_env) or os.environ.get(site.api_key_env)
        if not token:
            raise ConfigError(
                f"env var '{site.api_key_env}' (referenced by site '{site_name}') is not set"
            )
        client = VoogClient(host=site.host, api_token=token)
        self._cache[site_name] = client
        return client

    def list_sites(self) -> list[dict[str, str]]:
        return [{"name": s.name, "host": s.host} for s in self._global_cfg.sites.values()]


def _validate_resource_uri_patterns(groups) -> None:
    """Fail-fast on duplicate or overlapping resource URI patterns."""
    claims: list[tuple[str, str]] = []
    for group in groups:
        group_name = getattr(group, "__name__", repr(group))
        for pattern in group.get_uri_patterns():
            for existing_pattern, existing_group in claims:
                if pattern == existing_pattern:
                    raise RuntimeError(
                        f"Resource URI collision: pattern '{pattern}' claimed "
                        f"by both {existing_group} and {group_name}"
                    )
                if pattern.startswith(existing_pattern + "/") or existing_pattern.startswith(
                    pattern + "/"
                ):
                    raise RuntimeError(
                        f"Resource URI prefix overlap: '{pattern}' "
                        f"(from {group_name}) overlaps with '{existing_pattern}' "
                        f"(from {existing_group})"
                    )
            claims.append((pattern, group_name))


async def run_server(global_cfg: GlobalConfig, env: dict[str, str]):
    factory = ClientFactory(global_cfg, env)
    server = Server(name="voog-mcp", version="1.0.0")

    tool_dispatch: dict = {}
    for group in TOOL_GROUPS:
        for tool in group.get_tools():
            if tool.name in tool_dispatch:
                raise RuntimeError(
                    f"Tool name collision: '{tool.name}' defined in multiple tool groups"
                )
            tool_dispatch[tool.name] = group

    # Built-in discovery tool
    from mcp.types import Tool

    list_sites_tool = Tool(
        name="voog_list_sites",
        description="List all sites configured in the global voog.json. Returns "
        "[{name, host}, ...]. Call this first to see what sites are "
        "available before invoking any other voog_* tool.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    )

    _validate_resource_uri_patterns(RESOURCE_GROUPS)

    @server.list_tools()
    async def handle_list_tools():
        tools = [list_sites_tool]
        for group in TOOL_GROUPS:
            tools.extend(group.get_tools())
        return tools

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None):
        arguments = arguments or {}
        if name == "voog_list_sites":
            sites = factory.list_sites()
            return [{"type": "text", "text": str(sites)}]
        group = tool_dispatch.get(name)
        if group is None:
            return error_response(f"Unknown tool: {name}")
        site_name = arguments.get("site")
        if not site_name:
            return error_response(
                f"tool '{name}' requires a 'site' argument. "
                f"Available: {[s['name'] for s in factory.list_sites()]}"
            )
        try:
            client = factory.for_site(site_name)
        except ConfigError as exc:
            return error_response(str(exc))
        return await asyncio.to_thread(group.call_tool, name, arguments, client)

    @server.list_resources()
    async def handle_list_resources():
        return [r for group in RESOURCE_GROUPS for r in group.get_resources()]

    @server.read_resource()
    async def handle_read_resource(uri):
        uri_str = str(uri)
        for group in RESOURCE_GROUPS:
            if group.matches(uri_str):
                site_name = _extract_site_from_uri(uri_str)
                client = factory.for_site(site_name)
                return await asyncio.to_thread(group.read_resource, uri_str, client)
        raise ValueError(f"Unknown resource URI: {uri_str}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _extract_site_from_uri(uri: str) -> str:
    """Parse the site name from voog://<site>/... format."""
    if not uri.startswith("voog://"):
        raise ValueError(f"resource URI must start with voog://: {uri}")
    rest = uri[len("voog://") :]
    site, _, _ = rest.partition("/")
    if not site:
        raise ValueError(f"no site in URI: {uri}")
    return site


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    parser = argparse.ArgumentParser(prog="voog-mcp", description="Voog MCP server")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to global config (default: {default_global_config_path()} or $VOOG_CONFIG)",
    )
    args = parser.parse_args()

    config_path = args.config or (
        Path(os.environ["VOOG_CONFIG"]) if os.environ.get("VOOG_CONFIG") else None
    )
    try:
        global_cfg = load_global_config(config_path)
    except ConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        sys.exit(1)

    env_path = find_env_file(global_cfg, Path.cwd())
    env = load_env_file(env_path) if env_path else {}

    asyncio.run(run_server(global_cfg, env))


if __name__ == "__main__":
    main()
