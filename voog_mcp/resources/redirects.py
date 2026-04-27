"""MCP resource for Voog redirect rules."""
import json
from collections.abc import Iterable

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


async def read_resource(uri: str, client: VoogClient) -> Iterable[ReadResourceContents]:
    if uri != URI:
        raise ValueError(f"redirects resource: unsupported URI {uri!r}")
    rules = client.get_all("/redirect_rules")
    return [
        ReadResourceContents(
            content=json.dumps(rules, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]
