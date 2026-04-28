"""MCP resource for Voog redirect rules.

Single URI shape: ``voog://{site}/redirects``. Returns a list of redirect-rule objects.
"""
import re

from mcp.types import Resource

from voog.client import VoogClient
from voog.mcp.resources._helpers import ReadResourceContents, json_response


URI_TEMPLATE = "voog://{site}/redirects"
_URI_RE = re.compile(r"^voog://[^/]+/redirects$")


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_TEMPLATE]


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_TEMPLATE,
            name="Redirect rules",
            description="All redirect rules on the Voog site (id, source, destination, redirect_type, active).",
            mimeType="application/json",
        ),
    ]


def matches(uri: str) -> bool:
    return bool(_URI_RE.match(uri))


def _strip_site(uri: str) -> str:
    """voog://stella/redirects → /redirects"""
    rest = uri[len("voog://"):]
    _, _, path = rest.partition("/")
    return "/" + path


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    local = _strip_site(uri)
    if local != "/redirects":
        raise ValueError(f"redirects resource: unsupported URI {uri!r}")
    rules = client.get_all("/redirect_rules")
    return json_response(rules)
