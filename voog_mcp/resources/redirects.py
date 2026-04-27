"""MCP resource for Voog redirect rules.

Reference implementation for the Phase D resource group contract — Tasks 15-18
should mirror this shape:

  - Module-level URI / URI_PREFIX constant (single URI here; Tasks with multiple
    URIs like ``voog://pages``, ``voog://pages/{id}`` should define
    ``URI_PREFIX = "voog://pages"`` and prefix-match in :func:`matches`).
  - ``get_resources() -> list[Resource]``
  - ``matches(uri: str) -> bool``
  - ``async read_resource(uri, client) -> list[ReadResourceContents]``

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.
"""
import json

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient


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
    return [
        ReadResourceContents(
            content=json.dumps(rules, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]
