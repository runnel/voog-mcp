"""MCP resources for Voog layouts.

Two URI shapes:

  - ``voog://{site}/layouts``         — list all layouts (id, title, component,
                                         content_type, updated_at — body field
                                         stripped from the list view)
  - ``voog://{site}/layouts/{id}``    — raw layout body (.tpl source) as ``text/plain``
"""

import re

from mcp.types import Resource

from voog.client import VoogClient
from voog.mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
    text_response,
)
from voog.projections import simplify_layouts

URI_TEMPLATE = "voog://{site}/layouts"
_URI_RE = re.compile(r"^voog://[^/]+/layouts(/.*)?$")


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_TEMPLATE]


def matches(uri: str) -> bool:
    return bool(_URI_RE.match(uri))


def _strip_site(uri: str) -> str:
    """voog://stella/layouts/42 → /layouts/42"""
    rest = uri[len("voog://") :]
    _, _, path = rest.partition("/")
    return "/" + path


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_TEMPLATE,
            name="Layouts",
            description=(
                "All layouts on the Voog site (simplified: id, title, component, "
                "content_type, updated_at — without bodies). "
                "Single layout body (raw .tpl source) at voog://{site}/layouts/{id} as text/plain."
            ),
            mimeType="application/json",
        ),
    ]


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    local = _strip_site(uri)  # e.g. /layouts or /layouts/42

    if local == "/layouts":
        layouts = client.get_all("/layouts")
        return json_response(simplify_layouts(layouts))

    if not local.startswith("/layouts/"):
        raise ValueError(f"layouts resource: unsupported URI {uri!r}")

    sub = local[len("/layouts/") :]
    parts = sub.split("/")

    if len(parts) == 1:
        layout_id = parse_id(parts[0], uri, group_name="layouts")
        layout = client.get(f"/layouts/{layout_id}")
        return text_response(layout.get("body") or "", mime_type="text/plain")

    raise ValueError(f"layouts resource: unsupported URI {uri!r}")
