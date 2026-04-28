"""MCP resources for Voog layouts.

Phase D resource group covering two URI shapes:

  - ``voog://layouts``         — list all layouts (id, title, component,
                                  content_type, updated_at — body field
                                  intentionally stripped from the list view;
                                  bodies live at ``voog://layouts/{id}``)
  - ``voog://layouts/{id}``    — raw layout body (.tpl source) as ``text/plain``

The single-layout URI returns ``mime_type="text/plain"`` because the value is
the raw Liquid template, not JSON — constructed locally rather than via
:func:`json_response` for that reason. The list URI returns
``application/json`` via the shared helper.

Pattern mirrors :mod:`voog_mcp.resources.pages`: ``URI_PREFIX`` constant,
:func:`matches` exact-or-slashed-sub-path, errors propagate to the server
layer (no wrapping into MCP error responses).

The list view's curated shape comes from :func:`voog_mcp.projections.simplify_layouts`
(currently only used here — kept alongside the other simplify_* projections
for consistency, ready if a future tools-side surface needs the same shape).
"""
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.projections import simplify_layouts
from voog_mcp.resources._helpers import json_response, parse_id


URI_PREFIX = "voog://layouts"


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


def matches(uri: str) -> bool:
    return uri == URI_PREFIX or uri.startswith(URI_PREFIX + "/")


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
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
        body = layout.get("body") or ""
        return [
            ReadResourceContents(
                content=body,
                mime_type="text/plain",
            )
        ]

    raise ValueError(f"layouts resource: unsupported URI {uri!r}")
