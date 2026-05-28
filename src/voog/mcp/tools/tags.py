"""MCP tools for Voog tags.

Three tools — `tags_list`, `tag_get`, `tag_delete`. Tags are
auto-created by Voog when articles or content reference them; explicit
creation is rare, so this module is read-and-delete-only. To rename a
tag (rare ops), fall back to voog_admin_api_call.

`tag_delete` is force-gated (mirrors `comment_delete` / `element_delete` /
`webhook_delete`).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="tags_list",
            description=(
                "List all tags on the site (`GET /tags`). Returns the full "
                "Voog tag shape (id, name, slug, taggings_count, created_at). "
                "Useful for categorising articles / suggesting related "
                "content. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="tag_get",
            description=(
                "Get a single tag by id (`GET /tags/{id}`). Returns the full "
                "Voog tag shape. Use tags_list to discover ids. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "tag_id": {
                        "type": "integer",
                        "description": "Voog tag id (from tags_list)",
                    },
                },
                "required": ["site", "tag_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="tag_delete",
            description=(
                "Remove a tag (`DELETE /tags/{id}`). Voog returns 204. "
                "Requires force=true; without it the call is rejected. "
                "Run tags_list / tag_get first to confirm the id. Deletion "
                "removes the tag from all articles currently tagged with it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "tag_id": {
                        "type": "integer",
                        "description": "Voog tag id (from tags_list)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Must be true to actually perform the delete. "
                            "Defaults to false (defensive opt-in)."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "tag_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _tags_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        tags = client.get_all("/tags")
        return success_response(tags, summary=f"🏷️  {len(tags)} tags")
    except Exception as e:
        return error_response(f"tags_list failed: {e}")


def _tag_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    tag_id = arguments.get("tag_id")
    err = require_int("tag_id", tag_id, tool_name="tag_get")
    if err:
        return error_response(err)
    try:
        tag = client.get(f"/tags/{tag_id}")
        return success_response(tag)
    except Exception as e:
        return error_response(f"tag_get id={tag_id} failed: {e}")


def _tag_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    tag_id = arguments.get("tag_id")
    err = require_int("tag_id", tag_id, tool_name="tag_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="tag_delete",
        target_desc=f"tag {tag_id}",
        hint="Run tags_list first to confirm.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/tags/{tag_id}")
        return success_response(
            {"deleted": {"tag_id": tag_id}},
            summary=f"🗑️  tag {tag_id} deleted",
        )
    except Exception as e:
        return error_response(f"tag_delete id={tag_id} failed: {e}")


_DISPATCH = {
    "tags_list": _tags_list,
    "tag_get": _tag_get,
    "tag_delete": _tag_delete,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    # S9: tag every HTTP request inside this handler with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring.
    with client.with_tool(name):
        return handler(arguments, client)
