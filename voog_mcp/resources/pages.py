"""MCP resources for Voog pages.

Phase D resource group covering three URI shapes:

  - ``voog://pages``                       — list all pages (simplified)
  - ``voog://pages/{id}``                  — full page details
  - ``voog://pages/{id}/contents``         — page contents array

Multi-URI groups use ``URI_PREFIX`` (vs. single-URI groups using ``URI``);
:func:`matches` checks for the exact root *or* a slashed sub-path so that
URIs like ``voog://pagesx`` are correctly rejected.

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.
"""
import json

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient


URI_PREFIX = "voog://pages"
URI_ROOT = URI_PREFIX  # the listable URI


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_ROOT,
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
    return uri == URI_ROOT or uri.startswith(URI_ROOT + "/")


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_ROOT:
        pages = client.get_all("/pages")
        return _json_response(_simplify_pages(pages))

    if not uri.startswith(URI_ROOT + "/"):
        raise ValueError(f"pages resource: unsupported URI {uri!r}")

    sub = uri[len(URI_ROOT) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        page_id = _parse_id(parts[0], uri)
        page = client.get(f"/pages/{page_id}")
        return _json_response(page)

    if len(parts) == 2 and parts[1] == "contents":
        page_id = _parse_id(parts[0], uri)
        contents = client.get(f"/pages/{page_id}/contents")
        return _json_response(contents)

    raise ValueError(f"pages resource: unsupported URI {uri!r}")


def _parse_id(raw: str, uri: str) -> int:
    try:
        page_id = int(raw)
    except ValueError as e:
        raise ValueError(f"pages resource: invalid page id in {uri!r}") from e
    if page_id <= 0:
        raise ValueError(f"pages resource: page id must be positive in {uri!r}")
    return page_id


def _json_response(data) -> list[ReadResourceContents]:
    return [
        ReadResourceContents(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]


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
