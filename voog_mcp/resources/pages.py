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

The list view's curated shape comes from :func:`voog_mcp.projections.simplify_pages`,
shared with :mod:`voog_mcp.tools.pages` so the ``pages_list`` tool and the
``voog://pages`` resource can't drift out of sync.
"""
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.projections import simplify_pages
from voog_mcp.resources._helpers import json_response, parse_id, prefix_matcher


URI_PREFIX = "voog://pages"
matches = prefix_matcher(URI_PREFIX)


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


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        pages = client.get_all("/pages")
        return json_response(simplify_pages(pages))

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
