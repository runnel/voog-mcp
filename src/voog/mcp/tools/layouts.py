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

Note: ``layouts_pull`` / ``layouts_push`` (filesystem-touching, manifest-based)
live in :mod:`voog.mcp.tools.layouts_sync`; this module hosts the three pure-API
operations. The ``voog`` CLI also exposes both groups via ``voog pull`` /
``voog push`` for shell-driven workflows.

Pattern mirrors :mod:`voog.mcp.tools.pages_mutate`: explicit MCP annotations
on every tool (per PR #27 review — spec defaults destructiveHint=true when
readOnlyHint=false, so non-destructive mutating tools must be explicit).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site


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
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "layout_id": {"type": "integer", "description": "Voog layout id"},
                    "new_title": {"type": "string", "description": "New layout title"},
                },
                "required": ["site", "layout_id", "new_title"],
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
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
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
                "required": ["site", "title", "body", "kind"],
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
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "asset_id": {"type": "integer", "description": "Existing layout_asset id"},
                    "new_filename": {
                        "type": "string",
                        "description": "New filename (no '/', '\\', or leading '.')",
                    },
                },
                "required": ["site", "asset_id", "new_filename"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="layout_update",
            description=(
                "Update a layout — body (Liquid template source), title, "
                "or both. At least one must be supplied. Reversible by "
                "calling again with the previous values; idempotent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": "Liquid template source",
                    },
                },
                "required": ["site", "layout_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layout_delete",
            description=(
                "Delete a layout. IRREVERSIBLE — Voog does not retain "
                "deleted layouts. Refuses without force=true.\n\n"
                "Voog blocks deletion of layouts that still have pages assigned — "
                "the API returns an error, the layout is NOT deleted. Reassign "
                "those pages first via page_set_layout, then retry. Back up with "
                "site_snapshot before this operation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "layout_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="layout_asset_create",
            description=(
                "Create a layout_asset (CSS/JS/image). filename + asset_type "
                "+ data required. asset_type ∈ {stylesheet, javascript, "
                "image, plain_text, video, pdf, ...}. For image uploads, "
                "use POST /assets + 3-step protocol via product_set_images "
                "instead — this tool is for text assets (CSS/JS/HTML "
                "fragments)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "filename": {"type": "string"},
                    "asset_type": {"type": "string"},
                    "data": {
                        "type": "string",
                        "description": "Asset content (text)",
                    },
                },
                "required": ["site", "filename", "asset_type", "data"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="layout_asset_update",
            description=(
                "Update a layout_asset's content (PUT /layout_assets/{id} "
                "{data}). filename is read-only — Voog returns 500 if "
                "filename is sent on PUT. Use asset_replace to rename."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "asset_id": {"type": "integer"},
                    "data": {"type": "string"},
                    "filename": {
                        "type": "string",
                        "description": "REJECTED — use asset_replace to rename",
                    },
                },
                "required": ["site", "asset_id", "data"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layout_asset_delete",
            description=(
                "Delete a layout_asset. IRREVERSIBLE. Refuses without "
                "force=true. Templates referencing the deleted file will "
                "render with empty content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "asset_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "asset_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)


def _detect_silent_no_op(result, sent: dict, field: str) -> str | None:
    """Defense-in-depth check that the PUT actually persisted.

    Mirrors the narrow detector that ``voog push`` carried before #101
    moved to ``size`` / ``updated_at`` indirect signals: if the request
    sent a non-empty ``field`` and the response *includes* that field
    but it is empty/falsy, treat the response as a silent no-op
    (issue #96 symptom against ``layout_assets`` with a wrapped
    payload). This currently can't reproduce against the MCP path
    because the tools send the correct flat payload form, but a
    future regression — accidental envelope re-introduction, rate-
    limit anomaly, server-side change — would otherwise read back
    as ``✓`` while the content sat unchanged on Voog. (#99)

    Returns an error message if a silent no-op is detected, ``None``
    otherwise. Voog's slim PUT responses normally omit the content
    field entirely, so this falls through to ``None`` on every
    real response shape we've observed.
    """
    sent_value = sent.get(field)
    if not sent_value:
        return None
    if not isinstance(result, dict):
        return None
    if field not in result:
        return None
    if not result[field]:
        return (
            f"response echoed back with `{field}` cleared — content "
            "NOT updated on Voog (silent no-op symptom)"
        )
    return None


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
    err = require_int("layout_id", layout_id, tool_name="layout_rename")
    if err:
        return error_response(err)
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
        return error_response(f"layout_rename id={layout_id} failed: {e}")


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
            return error_response(f"layout_create: POST response missing 'id' field: {result!r}")
        return success_response(
            result,
            summary=f"✨ created {kind} {new_id}: {title!r}",
        )
    except Exception as e:
        return error_response(f"layout_create title={title!r} failed: {e}")


def _asset_replace(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    asset_id = arguments.get("asset_id")
    err = require_int("asset_id", asset_id, tool_name="asset_replace")
    if err:
        return error_response(err)
    new_filename = arguments.get("new_filename", "")
    err = _validate_voog_name(new_filename, "new_filename")
    if err:
        return error_response(f"asset_replace: {err}")

    try:
        old_asset = client.get(f"/layout_assets/{asset_id}")
    except Exception as e:
        return error_response(f"asset_replace: GET old asset {asset_id} failed: {e}")

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
        result = client.post(
            "/layout_assets",
            {
                "filename": new_filename,
                "asset_type": asset_type,
                "data": content,
            },
        )
    except Exception as e:
        return error_response(f"asset_replace: POST new asset failed: {e}")

    new_id = result.get("id")
    if not new_id:
        return error_response(f"asset_replace: POST response missing 'id' field: {result!r}")

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
        summary=f"✨ asset {asset_id} ({old_filename!r}) → {new_id} ({new_filename!r}) (old asset NOT deleted)",
    )


def _layout_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    layout_id = arguments.get("layout_id")
    err = require_int("layout_id", layout_id, tool_name="layout_update")
    if err:
        return error_response(err)
    body: dict = {}
    if arguments.get("title") is not None:
        title = arguments["title"]
        err = _validate_voog_name(title, "title")
        if err:
            return error_response(f"layout_update: {err}")
        body["title"] = title
    if arguments.get("body") is not None:
        body["body"] = arguments["body"]
    if not body:
        return error_response("layout_update: at least one of title/body required")
    try:
        result = client.put(f"/layouts/{layout_id}", body)
    except Exception as e:
        return error_response(f"layout_update id={layout_id} failed: {e}")
    err = _detect_silent_no_op(result, body, "body")
    if err:
        return error_response(f"layout_update id={layout_id}: {err}")
    return success_response(
        result,
        summary=f"✏️  layout {layout_id} updated ({sorted(body.keys())})",
    )


def _layout_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    layout_id = arguments.get("layout_id")
    err = require_int("layout_id", layout_id, tool_name="layout_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="layout_delete",
        target_desc=f"layout {layout_id}",
        hint=(
            "Voog blocks deleting a layout that still has pages assigned — "
            "reassign those pages via page_set_layout first, and back up with "
            "site_snapshot."
        ),
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/layouts/{layout_id}")
        return success_response(
            {"deleted": layout_id},
            summary=f"🗑️  layout {layout_id} deleted",
        )
    except Exception as e:
        return error_response(f"layout_delete id={layout_id} failed: {e}")


def _layout_asset_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    filename = arguments.get("filename") or ""
    asset_type = arguments.get("asset_type") or ""
    data = arguments.get("data")
    err = _validate_voog_name(filename, "filename")
    if err:
        return error_response(f"layout_asset_create: {err}")
    if not asset_type:
        return error_response("layout_asset_create: asset_type is required")
    if data is None:
        return error_response("layout_asset_create: data is required")
    try:
        result = client.post(
            "/layout_assets",
            {"filename": filename, "asset_type": asset_type, "data": data},
        )
        return success_response(
            result,
            summary=f"📁 layout_asset {result.get('id')} created: {filename}",
        )
    except Exception as e:
        return error_response(f"layout_asset_create failed: {e}")


def _layout_asset_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    asset_id = arguments.get("asset_id")
    err = require_int("asset_id", asset_id, tool_name="layout_asset_update")
    if err:
        return error_response(err)
    if "filename" in arguments and arguments["filename"]:
        return error_response(
            "layout_asset_update: filename is read-only on PUT (Voog "
            "returns 500). Use asset_replace to rename via DELETE+POST."
        )
    if arguments.get("data") is None:
        return error_response("layout_asset_update: data is required")
    payload = {"data": arguments["data"]}
    try:
        result = client.put(f"/layout_assets/{asset_id}", payload)
    except Exception as e:
        return error_response(f"layout_asset_update id={asset_id} failed: {e}")
    err = _detect_silent_no_op(result, payload, "data")
    if err:
        return error_response(f"layout_asset_update id={asset_id}: {err}")
    return success_response(
        result,
        summary=f"📁 layout_asset {asset_id} content updated",
    )


def _layout_asset_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    asset_id = arguments.get("asset_id")
    err = require_int("asset_id", asset_id, tool_name="layout_asset_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="layout_asset_delete",
        target_desc=f"layout asset {asset_id}",
        hint="Templates referencing it will break.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/layout_assets/{asset_id}")
        return success_response(
            {"deleted": asset_id},
            summary=f"🗑️  layout_asset {asset_id} deleted",
        )
    except Exception as e:
        return error_response(f"layout_asset_delete id={asset_id} failed: {e}")


_DISPATCH = {
    "layout_rename": _layout_rename,
    "layout_create": _layout_create,
    "asset_replace": _asset_replace,
    "layout_update": _layout_update,
    "layout_delete": _layout_delete,
    "layout_asset_create": _layout_asset_create,
    "layout_asset_update": _layout_asset_update,
    "layout_asset_delete": _layout_asset_delete,
}
