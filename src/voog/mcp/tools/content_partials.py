"""MCP tool for Voog content partials.

One tool — `content_partial_update`. Voog content_partials are reusable
template fragments (`{% content_partial 'name' %}`) embedded in many
pages; this tool enables editing them directly without the
layouts_pull / layouts_push filesystem detour.

Per the Voog API doc (https://www.voog.com/developers/api/resources/content_partials)
PUT body is FLAT — no `{"content_partial": {...}}` envelope. Accepts
`body` (HTML/text) and `metainfo` (object, with optional `type` ∈
{custom, map, video}). At least one of the two must be supplied.

POST is not supported by the Voog API — this module is update-only.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="content_partial_update",
            description=(
                "Update a content partial (PUT /content_partials/{id}). "
                "Content partials are reusable template fragments embedded "
                "in pages and layouts. PUT body is flat (no envelope). "
                "At least one of `body` (HTML/text content) or `metainfo` "
                "(object) must be supplied. Update is idempotent — calling "
                "with the same payload twice has the same end state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "content_partial_id": {"type": "integer"},
                    "body": {
                        "type": "string",
                        "description": "New body content (HTML or text).",
                    },
                    "metainfo": {
                        "type": "object",
                        "description": (
                            "Metainfo object. Voog accepts `type` in "
                            "{custom, map, video} among other implementation-"
                            "specific keys."
                        ),
                    },
                },
                "required": ["site", "content_partial_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "content_partial_update":
        return _content_partial_update(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _content_partial_update(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    cp_id = arguments.get("content_partial_id")
    body = arguments.get("body")
    metainfo = arguments.get("metainfo")

    if body is None and metainfo is None:
        return error_response(
            "content_partial_update: at least one of `body` or `metainfo` "
            "must be supplied"
        )

    payload: dict = {}
    if body is not None:
        payload["body"] = body
    if metainfo is not None:
        payload["metainfo"] = metainfo

    try:
        result = client.put(f"/content_partials/{cp_id}", payload)
        fields_changed = sorted(payload.keys())
        return success_response(
            result,
            summary=f"📄 content_partial {cp_id} updated: {fields_changed}",
        )
    except Exception as e:
        return error_response(
            f"content_partial_update id={cp_id} failed: {e}"
        )
