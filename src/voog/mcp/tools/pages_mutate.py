"""MCP tools for mutating Voog pages — set hidden, set layout, delete.

Three tools:

  - ``page_set_hidden``  — bulk toggle hidden flag across page ids (reversible)
  - ``page_set_layout``  — reassign a page's layout_id (reversible)
  - ``page_delete``      — DELETE a page (irreversible, ``destructiveHint=True``,
                            requires explicit ``force=True``)

Pattern mirrors :mod:`voog_mcp.tools.pages` (read-only): each tool returns
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
from voog.errors import success_response, error_response
from voog.mcp.tools._helpers import strip_site


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
    ]


def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "page_set_hidden":
        return _page_set_hidden(arguments, client)

    if name == "page_set_layout":
        return _page_set_layout(arguments, client)

    if name == "page_delete":
        return _page_delete(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _page_set_hidden(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    ids = arguments.get("ids") or []
    hidden = bool(arguments.get("hidden"))
    if not ids:
        return error_response("page_set_hidden: ids must be a non-empty list")

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
