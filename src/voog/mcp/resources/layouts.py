"""MCP resources for Voog layouts.

Two URI shapes:

  - ``voog://layouts``         — list all layouts (id, title, component,
                                  content_type, updated_at — body field
                                  stripped from the list view)
  - ``voog://layouts/{id}``    — raw layout body (.tpl source) as ``text/plain``
"""
from mcp.types import Resource

from voog.client import VoogClient
from voog.projections import simplify_layouts
from voog.mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
    prefix_matcher,
    text_response,
)


URI_PREFIX = "voog://layouts"
matches = prefix_matcher(URI_PREFIX)


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_PREFIX]


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_PREFIX,
            name="Layouts",
            description=(
                "All layouts on the Voog site (simplified: id, title, component, "
                "content_type, updated_at — without bodies). "
                "Single layout body (raw .tpl source) at voog://layouts/{id} as text/plain."
            ),
            mimeType="application/json",
        ),
    ]


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        layouts = client.get_all("/layouts")
        return json_response(simplify_layouts(layouts))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"layouts resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        layout_id = parse_id(parts[0], uri, group_name="layouts")
        layout = client.get(f"/layouts/{layout_id}")
        return text_response(layout.get("body") or "", mime_type="text/plain")

    raise ValueError(f"layouts resource: unsupported URI {uri!r}")
