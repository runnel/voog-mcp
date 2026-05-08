"""MCP tools for mutating Voog pages — set hidden, set layout, delete, create, update, set_data, delete_data, duplicate.

Eight tools:

  - ``page_set_hidden``   — bulk toggle hidden flag across page ids (reversible)
  - ``page_set_layout``   — reassign a page's layout_id (reversible)
  - ``page_delete``       — DELETE a page (irreversible, ``destructiveHint=True``,
                             requires explicit ``force=True``)
  - ``page_create``       — POST /pages (root, subpage, or parallel translation)
  - ``page_update``       — PUT /pages/{id} (general field updates)
  - ``page_set_data``     — PUT /pages/{id}/data/{key} (PUT-only, non-destructive)
  - ``page_delete_data``  — DELETE /pages/{id}/data/{key} (requires ``force=True``)
  - ``page_duplicate``    — POST /pages/{id}/duplicate

Pattern mirrors :mod:`voog.mcp.tools.pages` (read-only): each tool returns
``success_response`` with a human-readable summary plus the JSON result, or
``error_response`` on failure. Bulk operations (``page_set_hidden``) return a
structured per-id breakdown rather than failing fast — the caller sees which
ids succeeded and which didn't.

The two non-destructive tools omit ``destructiveHint``; only ``page_delete``
sets it. The ``force`` parameter on ``page_delete`` is a defensive in-band
opt-in alongside the MCP annotation, so the actual API call is gated on a
boolean flag the LLM has to explicitly pass.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog._concurrency import parallel_map
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import _validate_data_key, require_int, strip_site


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="page_set_hidden",
            description=(
                "Bulk toggle the `hidden` flag on one or more pages. Reversible — "
                "set hidden=false to make pages visible again. Returns a per-id "
                "breakdown showing which ids succeeded and which failed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Voog page ids to update",
                        "minItems": 1,
                    },
                    "hidden": {
                        "type": "boolean",
                        "description": "true to hide, false to show",
                    },
                },
                "required": ["site", "ids", "hidden"],
            },
            # Explicit annotations — MCP spec defaults destructiveHint to true
            # when readOnlyHint is false. Setters are mutating but reversible
            # (call again with the inverse value), so we mark them
            # destructiveHint=False explicitly. idempotentHint=True because
            # repeated identical calls produce the same end state.
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_set_layout",
            description=(
                "Reassign a page's layout. Reversible — call again with the "
                "original layout_id to revert."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "page_id": {"type": "integer", "description": "Voog page id"},
                    "layout_id": {"type": "integer", "description": "Voog layout id"},
                },
                "required": ["site", "page_id", "layout_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_delete",
            description=(
                "Delete a page. IRREVERSIBLE — Voog does not retain deleted pages. "
                "Requires force=true; without it the call is rejected to prevent "
                "accidental deletion. Run `pages_snapshot` or `site_snapshot` first "
                "if the page might be needed later."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "page_id": {"type": "integer", "description": "Voog page id"},
                    "force": {
                        "type": "boolean",
                        "description": "Must be true to actually perform the delete. Defaults to false (defensive opt-in).",
                        "default": False,
                    },
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="page_create",
            description=(
                "Create a new page. Required: title, slug, language_id. "
                "Optional: parent_id (page id, NOT node_id) for subpages, "
                "node_id for parallel-translation pages of an existing "
                "page in another language, layout_id, content_type "
                "('page'|'link'|'blog'|'product'|...), hidden, image_id, "
                "description, keywords, data (custom dict).\n"
                "Multilingual: pass node_id of the first-language page "
                "instead of parent_id when creating its translation in "
                "another language. Voog binds them as parallels (admin "
                "Translate UI works correctly). parent_id and node_id are "
                "mutually exclusive."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "language_id": {"type": "integer"},
                    "parent_id": {"type": "integer"},
                    "node_id": {"type": "integer"},
                    "layout_id": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "hidden": {"type": "boolean"},
                    "image_id": {"type": "integer"},
                    "description": {"type": "string"},
                    "keywords": {"type": "string"},
                    "data": {"type": "object"},
                    "publishing": {"type": "boolean"},
                },
                "required": ["site", "title", "slug", "language_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="page_update",
            description=(
                "Update arbitrary fields on a page. At least one of "
                "title, slug, layout_id, image_id, content_type, "
                "parent_id, description, keywords, data must be supplied. "
                "For just hidden / layout id, prefer the dedicated "
                "page_set_hidden / page_set_layout — they're more explicit "
                "in tool listings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "image_id": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "parent_id": {"type": "integer"},
                    "description": {"type": "string"},
                    "keywords": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_set_data",
            description=(
                "Set a single page.data.<key> value (PUT /pages/{id}/data/{key}). "
                "To delete a key use page_delete_data. "
                "Keys starting with 'internal_' are server-protected and "
                "rejected client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array"],
                        "description": (
                            "New value for page.data.<key>. Any JSON value "
                            "EXCEPT null — to remove a key, use "
                            "page_delete_data instead. Nested objects and "
                            "arrays are stored as-is and round-tripped on read."
                        ),
                    },
                },
                "required": ["site", "page_id", "key", "value"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_delete_data",
            description=(
                "Delete a single page.data.<key> (DELETE /pages/{id}/data/{key}). "
                "IRREVERSIBLE — the key is removed permanently. "
                "Requires force=true; without it the call is rejected. "
                "Keys starting with 'internal_' are server-protected and "
                "rejected client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "key": {"type": "string"},
                    "force": {
                        "type": "boolean",
                        "description": "Must be true to actually perform the delete. Defaults to false (defensive opt-in).",
                        "default": False,
                    },
                },
                "required": ["site", "page_id", "key"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="page_duplicate",
            description=(
                "POST /pages/{id}/duplicate — create a copy of the page "
                "(including its content). The new page is hidden by "
                "default per Voog convention."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
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


def _page_set_hidden(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    ids = arguments.get("ids") or []
    hidden = bool(arguments.get("hidden"))
    if not ids:
        return error_response("page_set_hidden: ids must be a non-empty list")
    for pid in ids:
        err = require_int("ids[]", pid, tool_name="page_set_hidden")
        if err:
            return error_response(err)

    def _put_one(pid):
        return client.put(f"/pages/{pid}", {"hidden": hidden})

    # Writes are more sensitive than reads — max_workers=4 (spec § 4.3).
    parallel_results = parallel_map(_put_one, ids, max_workers=4)

    results = []
    for pid, _, exc in parallel_results:
        if exc is None:
            results.append({"id": pid, "ok": True})
        else:
            results.append({"id": pid, "ok": False, "error": str(exc)})

    succeeded = sum(1 for r in results if r["ok"])
    failed = len(results) - succeeded
    flag = "🔒 hidden" if hidden else "👁  visible"
    summary = f"{flag}: {succeeded}/{len(results)} pages updated"
    if failed:
        summary += f" ({failed} failed)"
    return success_response(
        {"total": len(results), "succeeded": succeeded, "failed": failed, "results": results},
        summary=summary,
    )


def _page_set_layout(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    layout_id = arguments.get("layout_id")
    err = require_int("page_id", page_id, tool_name="page_set_layout")
    if err:
        return error_response(err)
    err = require_int("layout_id", layout_id, tool_name="page_set_layout")
    if err:
        return error_response(err)
    try:
        result = client.put(f"/pages/{page_id}", {"layout_id": layout_id})
        return success_response(
            result,
            summary=f"✓ page {page_id} → layout {layout_id}",
        )
    except Exception as e:
        return error_response(f"page_set_layout page={page_id} layout={layout_id} failed: {e}")


def _page_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    err = require_int("page_id", page_id, tool_name="page_delete")
    if err:
        return error_response(err)
    force = bool(arguments.get("force"))
    if not force:
        return error_response(
            f"page_delete: refusing to delete page {page_id} without force=true. "
            "Set force=true after confirming the deletion is intentional. "
            "Voog does not retain deleted pages — consider running pages_snapshot first."
        )
    try:
        client.delete(f"/pages/{page_id}")
        # API returns 204 No Content — no body to echo back
        return success_response(
            {"deleted": page_id},
            summary=f"🗑️  page {page_id} deleted",
        )
    except Exception as e:
        return error_response(f"page_delete page={page_id} failed: {e}")


PAGE_UPDATE_FIELDS = (
    "title",
    "slug",
    "layout_id",
    "image_id",
    "content_type",
    "parent_id",
    "description",
    "keywords",
    "data",
)

# Whitelist for `page.content_type` on POST /pages. Voog accepts a fixed set
# (per docs/voog-mcp-endpoint-coverage.md and the page_create docstring) —
# typo'd values 422 at the API level. Validate locally so the LLM gets a
# clear, actionable error listing the known-good set.
VALID_PAGE_CONTENT_TYPES = frozenset(
    {
        "page",
        "link",
        "blog",
        "product",
        "category",
        "gallery",
        "form",
        "news",
    }
)


def _page_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    language_id = arguments.get("language_id")
    err = require_int("language_id", language_id, tool_name="page_create")
    if err:
        return error_response(err)
    for opt_field in ("parent_id", "node_id", "layout_id", "image_id"):
        val = arguments.get(opt_field)
        if val is not None:
            err = require_int(opt_field, val, tool_name="page_create")
            if err:
                return error_response(err)
    if arguments.get("node_id") is not None and arguments.get("parent_id") is not None:
        return error_response(
            "page_create: node_id and parent_id are mutually exclusive — "
            "use node_id for parallel translations, parent_id for subpages, "
            "or omit both for root pages."
        )
    content_type = arguments.get("content_type")
    if content_type is not None and content_type not in VALID_PAGE_CONTENT_TYPES:
        return error_response(
            f"page_create: content_type {content_type!r} not in known-good set. "
            f"Allowed: {sorted(VALID_PAGE_CONTENT_TYPES)}"
        )
    body: dict = {
        "title": arguments.get("title"),
        "slug": arguments.get("slug"),
        "language_id": arguments.get("language_id"),
    }
    for key in (
        "parent_id",
        "node_id",
        "layout_id",
        "content_type",
        "hidden",
        "image_id",
        "description",
        "keywords",
        "data",
        "publishing",
    ):
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post("/pages", body)
        return success_response(
            result,
            summary=f"📄 page {result.get('id')} created at /{result.get('path', '')}",
        )
    except Exception as e:
        return error_response(f"page_create failed: {e}")


def _page_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    err = require_int("page_id", page_id, tool_name="page_update")
    if err:
        return error_response(err)
    for opt_field in ("layout_id", "image_id", "parent_id"):
        val = arguments.get(opt_field)
        if val is not None:
            err = require_int(opt_field, val, tool_name="page_update")
            if err:
                return error_response(err)
    # Self-parent cycle guard: parent_id == page_id would create a self-
    # referential parent. Mirrors the mutex pattern in _page_create.
    parent_id = arguments.get("parent_id")
    if parent_id is not None and parent_id == page_id:
        return error_response(
            f"page_update: parent_id ({parent_id}) must not equal page_id "
            f"({page_id}) — would create a self-parent cycle."
        )
    body: dict = {}
    for key in PAGE_UPDATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(f"page_update: at least one of {PAGE_UPDATE_FIELDS} must be supplied")
    try:
        result = client.put(f"/pages/{page_id}", body)
        return success_response(
            result,
            summary=f"📄 page {page_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"page_update id={page_id} failed: {e}")


def _page_set_data(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    err = require_int("page_id", page_id, tool_name="page_set_data")
    if err:
        return error_response(err)
    key = arguments.get("key") or ""
    value = arguments.get("value")

    err = _validate_data_key(key, tool_name="page_set_data")
    if err:
        return error_response(err)
    try:
        result = client.put(f"/pages/{page_id}/data/{key}", {"value": value})
        return success_response(
            result,
            summary=f"📄 page {page_id} data.{key} set",
        )
    except Exception as e:
        return error_response(f"page_set_data page={page_id} key={key!r} failed: {e}")


def _page_delete_data(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    err = require_int("page_id", page_id, tool_name="page_delete_data")
    if err:
        return error_response(err)
    key = arguments.get("key") or ""
    force = bool(arguments.get("force"))

    err = _validate_data_key(key, tool_name="page_delete_data")
    if err:
        return error_response(err)
    if not force:
        return error_response(
            f"page_delete_data: refusing to delete page {page_id} data.{key!r} without force=true. "
            "Set force=true after confirming the deletion is intentional."
        )
    try:
        client.delete(f"/pages/{page_id}/data/{key}")
        return success_response(
            {"deleted": {"page_id": page_id, "key": key}},
            summary=f"🗑️  page {page_id} data.{key} deleted",
        )
    except Exception as e:
        return error_response(f"page_delete_data page={page_id} key={key!r} failed: {e}")


def _page_duplicate(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    err = require_int("page_id", page_id, tool_name="page_duplicate")
    if err:
        return error_response(err)
    try:
        result = client.post(f"/pages/{page_id}/duplicate", {})
        new_id = result.get("id")
        # Voog returns duplicated pages as hidden by default; surface that
        # so the LLM caller knows to call page_set_hidden(false) before the
        # duplicate is publicly visible.
        suffix = " (hidden, use page_set_hidden(false) to publish)" if result.get("hidden") else ""
        summary = f"📑 page {page_id} duplicated → {new_id}{suffix}"
        return success_response(result, summary=summary)
    except Exception as e:
        return error_response(f"page_duplicate id={page_id} failed: {e}")


_DISPATCH = {
    "page_set_hidden": _page_set_hidden,
    "page_set_layout": _page_set_layout,
    "page_delete": _page_delete,
    "page_create": _page_create,
    "page_update": _page_update,
    "page_set_data": _page_set_data,
    "page_delete_data": _page_delete_data,
    "page_duplicate": _page_duplicate,
}
