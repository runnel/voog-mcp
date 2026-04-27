"""MCP resource for Voog redirect rules.

Reference implementation for the Phase D resource group contract:

  - Module-level URI / URI_PREFIX constant (single URI here; multi-URI groups
    like ``voog://pages``, ``voog://pages/{id}`` define ``URI_PREFIX`` and
    prefix-match in :func:`matches`).
  - ``get_resources() -> list[Resource]``
  - ``matches(uri: str) -> bool``
  - ``async read_resource(uri, client) -> list[ReadResourceContents]``

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors. JSON wrapping uses the shared
:func:`voog_mcp.resources._helpers.json_response` helper.
"""
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.resources._helpers import json_response


URI = "voog://redirects"


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI,
            name="Redirect rules",
            description="All redirect rules on the Voog site (id, source, destination, redirect_type, active).",
            mimeType="application/json",
        ),
    ]


def matches(uri: str) -> bool:
    return uri == URI


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri != URI:
        raise ValueError(f"redirects resource: unsupported URI {uri!r}")
    rules = client.get_all("/redirect_rules")
    return json_response(rules)
