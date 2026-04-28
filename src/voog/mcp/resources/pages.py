"""MCP resources for Voog pages.

Phase D resource group covering three URI shapes:

  - ``voog://{site}/pages``                       — list all pages (simplified)
  - ``voog://{site}/pages/{id}``                  — full page details
  - ``voog://{site}/pages/{id}/contents``         — page contents array

Multi-URI groups use a single ``URI_TEMPLATE`` constant; :func:`matches` accepts
any ``voog://<site>/pages`` URI so that URIs like ``voog://<site>/pagesx`` are
correctly rejected. The ``{site}`` segment is stripped before dispatching to the
API client so the rest of the logic remains site-agnostic.

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.

The list view's curated shape comes from :func:`voog_mcp.projections.simplify_pages`,
shared with :mod:`voog_mcp.tools.pages` so the ``pages_list`` tool and the
``voog://{site}/pages`` resource can't drift out of sync.
"""
import re

from mcp.types import Resource

from voog.client import VoogClient
from voog.projections import simplify_pages
from voog.mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
)


URI_TEMPLATE = "voog://{site}/pages"
_URI_RE = re.compile(r"^voog://[^/]+/pages(/.*)?$")


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_TEMPLATE]


def matches(uri: str) -> bool:
    return bool(_URI_RE.match(uri))


def _strip_site(uri: str) -> str:
    """voog://stella/pages/42 → /pages/42"""
    rest = uri[len("voog://"):]
    _, _, path = rest.partition("/")
    return "/" + path


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_TEMPLATE,
            name="Pages",
            description=(
                "All pages on the Voog site (simplified: id, path, title, hidden, "
                "layout, content_type, language, public_url). "
                "Per-page details available at voog://{site}/pages/{id}, "
                "page contents at voog://{site}/pages/{id}/contents."
            ),
            mimeType="application/json",
        ),
    ]


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    local = _strip_site(uri)  # e.g. /pages or /pages/42 or /pages/42/contents

    if local == "/pages":
        pages = client.get_all("/pages")
        return json_response(simplify_pages(pages))

    if not local.startswith("/pages/"):
        raise ValueError(f"pages resource: unsupported URI {uri!r}")

    sub = local[len("/pages/"):]
    parts = sub.split("/")

    if len(parts) == 1:
        page_id = parse_id(parts[0], uri, group_name="pages")
        page = client.get(f"/pages/{page_id}")
        return json_response(page)

    if len(parts) == 2 and parts[1] == "contents":
        page_id = parse_id(parts[0], uri, group_name="pages")
        contents = client.get(f"/pages/{page_id}/contents")
        return json_response(contents)

    raise ValueError(f"pages resource: unsupported URI {uri!r}")
