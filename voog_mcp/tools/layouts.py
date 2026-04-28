"""MCP tools for Voog layouts (rename, create, asset replace).

Three pure-API tools:

  - ``layout_rename``   — PUT /layouts/{id} to change the title (reversible,
                           idempotent)
  - ``layout_create``   — POST /layouts to add a new layout/component (additive,
                           NOT idempotent — each call creates a new id)
  - ``asset_replace``   — DELETE+POST workaround for renaming layout_assets
                           (Voog API rejects PUT /layout_assets/{id} with a
                           filename change). Creates a NEW asset; the old one
                           is left in place for caller to delete after
                           updating templates that reference the old name.

Filesystem-touching tools (``layouts_pull`` / ``layouts_push``) are deferred
to a follow-up. The ``voog.py`` CLI shim still works for those — users can
invoke it via Bash for now, while these three pure-API tools handle the
common per-layout operations from MCP.

Pattern mirrors :mod:`voog_mcp.tools.pages_mutate`: explicit MCP annotations
on every tool (per PR #27 review — spec defaults destructiveHint=true when
readOnlyHint=false, so non-destructive mutating tools must be explicit).
"""
from mcp.types import CallToolResult, TextContent, Tool

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="layout_rename",
            description=(
                "Rename a layout (PUT /layouts/{id} {title}). Reversible — "
                "rename back to the original title to undo. The new title "
                "must not contain '/' or '\\' or start with '.'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "layout_id": {"type": "integer", "description": "Voog layout id"},
                    "new_title": {"type": "string", "description": "New layout title"},
                },
                "required": ["layout_id", "new_title"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layout_create",
            description=(
                "Create a new layout or component (POST /layouts). "
                "kind='layout' for full templates (defaults content_type='page'; "
                "use 'blog_article' for blog post templates); kind='component' "
                "for shared partials (content_type ignored). Returns the new id. "
                "NOT idempotent — calling twice creates two separate layouts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Layout title (no '/', '\\', or leading '.')",
                    },
                    "body": {
                        "type": "string",
                        "description": "Liquid template source code (.tpl content)",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["layout", "component"],
                        "description": "'layout' for full pages, 'component' for partials",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["page", "blog_article", "blog"],
                        "description": (
                            "Layout content_type — only sent when kind='layout'. "
                            "'page' (default) for full page templates; 'blog_article' "
                            "for individual post templates; 'blog' for blog index. "
                            "Ignored for components."
                        ),
                        "default": "page",
                    },
                },
                "required": ["title", "body", "kind"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="asset_replace",
            description=(
                "Rename a layout_asset by creating a new one with the desired "
                "filename (DELETE+POST workaround — Voog API rejects PUT with "
                "filename changes). Returns both old and new ids. The OLD asset "
                "is intentionally left in place; after updating templates that "
                "reference the old filename, delete the old asset manually with "
                "DELETE /layout_assets/{old_id}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "Existing layout_asset id"},
                    "new_filename": {
                        "type": "string",
                        "description": "New filename (no '/', '\\', or leading '.')",
                    },
                },
                "required": ["asset_id", "new_filename"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
    ]


async def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}

    if name == "layout_rename":
        return _layout_rename(arguments, client)

    if name == "layout_create":
        return _layout_create(arguments, client)

    if name == "asset_replace":
        return _asset_replace(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _validate_voog_name(value: str, field: str) -> str | None:
    """Voog title/filename rules: non-empty, no / or \\, no leading dot.

    Returns ``None`` if valid, or an error message string if invalid.
    """
    if not value:
        return f"{field} must be non-empty"
    if "/" in value or "\\" in value:
        return f"{field} must not contain '/' or '\\' (got {value!r})"
    if value.startswith("."):
        return f"{field} must not start with '.' (got {value!r})"
    return None


def _layout_rename(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    layout_id = arguments.get("layout_id")
    new_title = arguments.get("new_title", "")
    err = _validate_voog_name(new_title, "new_title")
    if err:
        return error_response(f"layout_rename: {err}")
    try:
        result = client.put(f"/layouts/{layout_id}", {"title": new_title})
        return success_response(
            result,
            summary=f"✓ layout {layout_id} → {new_title!r}",
        )
    except Exception as e:
        return error_response(f"layout_rename id={layout_id} ebaõnnestus: {e}")


def _layout_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    title = arguments.get("title", "")
    body = arguments.get("body", "")
    kind = arguments.get("kind", "")

    err = _validate_voog_name(title, "title")
    if err:
        return error_response(f"layout_create: {err}")
    if kind not in ("layout", "component"):
        return error_response(f"layout_create: kind must be 'layout' or 'component' (got {kind!r})")

    content_type = arguments.get("content_type") or "page"
    if content_type not in ("page", "blog_article", "blog"):
        return error_response(
            f"layout_create: content_type must be 'page', 'blog_article', or 'blog' (got {content_type!r})"
        )

    payload = {
        "title": title,
        "body": body,
        "component": (kind == "component"),
    }
    if kind == "layout":
        # Voog requires content_type for non-component layouts; HTTP 422 without it.
        # Default 'page' but allow 'blog_article'/'blog' for blog template variants.
        payload["content_type"] = content_type

    try:
        result = client.post("/layouts", payload)
        new_id = result.get("id")
        if not new_id:
            return error_response(
                f"layout_create: POST response missing 'id' field: {result!r}"
            )
        return success_response(
            result,
            summary=f"✨ created {kind} {new_id}: {title!r}",
        )
    except Exception as e:
        return error_response(f"layout_create title={title!r} ebaõnnestus: {e}")


def _asset_replace(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    asset_id = arguments.get("asset_id")
    new_filename = arguments.get("new_filename", "")
    err = _validate_voog_name(new_filename, "new_filename")
    if err:
        return error_response(f"asset_replace: {err}")

    try:
        old_asset = client.get(f"/layout_assets/{asset_id}")
    except Exception as e:
        return error_response(f"asset_replace: GET old asset {asset_id} ebaõnnestus: {e}")

    asset_type = old_asset.get("asset_type")
    old_filename = old_asset.get("filename")
    content = old_asset.get("data")

    if content is None:
        return error_response(
            f"asset_replace: old asset {asset_id} has no 'data' field — cannot "
            "replace without content. (Some asset types return data via a "
            "separate URL; in that case fetch the file manually and re-create "
            "with the desired filename.)"
        )

    try:
        result = client.post("/layout_assets", {
            "filename": new_filename,
            "asset_type": asset_type,
            "data": content,
        })
    except Exception as e:
        return error_response(f"asset_replace: POST new asset ebaõnnestus: {e}")

    new_id = result.get("id")
    if not new_id:
        return error_response(
            f"asset_replace: POST response missing 'id' field: {result!r}"
        )

    return success_response(
        {
            "old_id": asset_id,
            "old_filename": old_filename,
            "new_id": new_id,
            "new_filename": new_filename,
            "asset_type": asset_type,
            "warning": (
                f"Old asset id {asset_id} is still present. After updating "
                f"templates that reference {old_filename!r}, delete it with "
                f"DELETE /layout_assets/{asset_id}."
            ),
        },
        summary=f"🆕 asset {asset_id} ({old_filename!r}) → {new_id} ({new_filename!r}) (old asset NOT deleted)",
    )
