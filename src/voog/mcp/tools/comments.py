"""MCP tools for Voog article comments.

Three tools — `comments_list`, `comment_delete`, `comment_toggle_spam`.

Comments live under an article: the canonical path is
`/admin/api/articles/{article_id}/comments[/{comment_id}]`. All tools
require both `article_id` and (for individual operations) `comment_id`.

Body shapes are FLAT (no envelope). The `comment_toggle_spam` body is
`{is_spam: bool}` — flipping spam state is the only common moderation
operation, hence the dedicated wrapper (rather than a generic
`comment_update`). For other field edits, fall back to
`voog_admin_api_call` (passthrough) — comment author/body/email edits
are rare and would expand the surface unnecessarily.

`comment_delete` is force-gated (mirrors `webhook_delete` /
`element_delete` / `article_delete`).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="comments_list",
            description=(
                "List comments on an article (`GET /articles/{article_id}/comments`). "
                "Returns the full Voog comment shape (id, author, body, email, "
                "is_spam, created_at). Use article_id from articles_list. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {
                        "type": "integer",
                        "description": "Voog article id (from articles_list)",
                    },
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="comment_delete",
            description=(
                "Remove a comment (`DELETE /articles/{article_id}/comments/{comment_id}`). "
                "Voog returns 204. Requires force=true; without it the call is "
                "rejected. Run comments_list first to confirm the id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {
                        "type": "integer",
                        "description": "Voog article id (from articles_list)",
                    },
                    "comment_id": {
                        "type": "integer",
                        "description": "Voog comment id (from comments_list)",
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
                "required": ["site", "article_id", "comment_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="comment_toggle_spam",
            description=(
                "Flip a comment's spam flag (`PUT /articles/{article_id}/comments/"
                "{comment_id}`). Body is FLAT: `{is_spam: bool}`. Voog's "
                "moderation UI also uses this endpoint. For other field "
                "edits use voog_admin_api_call (rare)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {
                        "type": "integer",
                        "description": "Voog article id (from articles_list)",
                    },
                    "comment_id": {
                        "type": "integer",
                        "description": "Voog comment id (from comments_list)",
                    },
                    "is_spam": {
                        "type": "boolean",
                        "description": "New spam state (true = mark spam, false = unmark)",
                    },
                },
                "required": ["site", "article_id", "comment_id", "is_spam"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def _comments_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    article_id = arguments.get("article_id")
    err = require_int("article_id", article_id, tool_name="comments_list")
    if err:
        return error_response(err)
    try:
        comments = client.get_all(f"/articles/{article_id}/comments")
        return success_response(
            comments,
            summary=f"💬 {len(comments)} comments on article {article_id}",
        )
    except Exception as e:
        return error_response(f"comments_list article_id={article_id} failed: {e}")


def _comment_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    article_id = arguments.get("article_id")
    comment_id = arguments.get("comment_id")
    err = require_int("article_id", article_id, tool_name="comment_delete")
    if err:
        return error_response(err)
    err = require_int("comment_id", comment_id, tool_name="comment_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="comment_delete",
        target_desc=f"comment {comment_id} on article {article_id}",
        hint="Run comments_list first to confirm.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/articles/{article_id}/comments/{comment_id}")
        return success_response(
            {"deleted": {"article_id": article_id, "comment_id": comment_id}},
            summary=f"🗑️  comment {comment_id} on article {article_id} deleted",
        )
    except Exception as e:
        return error_response(
            f"comment_delete article_id={article_id} comment_id={comment_id} failed: {e}"
        )


def _comment_toggle_spam(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    article_id = arguments.get("article_id")
    comment_id = arguments.get("comment_id")
    is_spam = arguments.get("is_spam")
    err = require_int("article_id", article_id, tool_name="comment_toggle_spam")
    if err:
        return error_response(err)
    err = require_int("comment_id", comment_id, tool_name="comment_toggle_spam")
    if err:
        return error_response(err)
    if not isinstance(is_spam, bool):
        return error_response(
            f"comment_toggle_spam: is_spam must be a boolean (got {type(is_spam).__name__})"
        )
    try:
        result = client.put(
            f"/articles/{article_id}/comments/{comment_id}",
            {"is_spam": is_spam},
        )
        verb = "marked spam" if is_spam else "unmarked spam"
        return success_response(
            result,
            summary=f"💬 comment {comment_id} on article {article_id} {verb}",
        )
    except Exception as e:
        return error_response(
            f"comment_toggle_spam article_id={article_id} comment_id={comment_id} failed: {e}"
        )


_DISPATCH = {
    "comments_list": _comments_list,
    "comment_delete": _comment_delete,
    "comment_toggle_spam": _comment_toggle_spam,
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
