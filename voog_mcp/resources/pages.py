"""MCP resources for Voog pages.

Phase D resource group covering three URI shapes:

  - ``voog://pages``                       — list all pages (simplified)
  - ``voog://pages/{id}``                  — full page details
  - ``voog://pages/{id}/contents``         — page contents array

Multi-URI groups use a single ``URI_PREFIX`` constant (vs. single-URI groups
using ``URI``); :func:`matches` checks for the exact prefix *or* a slashed
sub-path so that URIs like ``voog://pagesx`` are correctly rejected. Future
groups whose listable URI differs from the prefix root (e.g. ``voog://articles``
prefix but ``voog://articles/published`` as the listable URI) can introduce
a separate ``URI_ROOT`` constant — pages, layouts, articles, products and
redirects all happen to have ``URI_PREFIX == listable URI``, so a single name
suffices.

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.

NOTE: ``_simplify_pages`` is deliberately duplicated from
``voog_mcp.tools.pages`` so the ``pages_list`` tool and the ``voog://pages``
resource produce the same shape. Group-specific projections rightly stay
local; only id parsing and JSON wrapping are shared via :mod:`._helpers`.
"""
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.resources._helpers import json_response, parse_id
from mcp.server.lowlevel.helper_types import ReadResourceContents


URI_PREFIX = "voog://pages"


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_PREFIX,
            name="Pages",
            description=(
                "All pages on the Voog site (simplified: id, path, title, hidden, "
                "layout, content_type, language, public_url). "
                "Per-page details available at voog://pages/{id}, "
                "page contents at voog://pages/{id}/contents."
            ),
            mimeType="application/json",
        ),
    ]


def matches(uri: str) -> bool:
    return uri == URI_PREFIX or uri.startswith(URI_PREFIX + "/")


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        pages = client.get_all("/pages")
        return json_response(_simplify_pages(pages))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"pages resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
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


def _simplify_pages(pages: list) -> list:
    """Project pages to simplified structure (matches voog_mcp.tools.pages)."""
    simplified = []
    for p in pages:
        lang = p.get("language") or {}
        layout = p.get("layout") or {}
        simplified.append({
            "id": p.get("id"),
            "path": p.get("path"),
            "title": p.get("title"),
            "hidden": p.get("hidden"),
            "layout_id": p.get("layout_id") or layout.get("id"),
            "layout_name": p.get("layout_name") or p.get("layout_title") or layout.get("title"),
            "content_type": p.get("content_type"),
            "parent_id": p.get("parent_id"),
            "language_code": lang.get("code"),
            "public_url": p.get("public_url"),
        })
    return simplified
