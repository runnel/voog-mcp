"""MCP resource for Voog redirect rules.

Single URI: ``voog://redirects``. Returns a list of redirect-rule objects.
"""
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.resources._helpers import ReadResourceContents, json_response


URI = "voog://redirects"


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI]


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


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri != URI:
        raise ValueError(f"redirects resource: unsupported URI {uri!r}")
    rules = client.get_all("/redirect_rules")
    return json_response(rules)
