"""MCP tools for Voog webhooks.

Four tools — `webhooks_list`, `webhook_create`, `webhook_update`,
`webhook_delete`. Dispatch via `_DISPATCH` dict (codebase convention
for ≥4-tool modules).

Webhook target+event matrix per Voog docs:
  - target "ticket" → events: create, update, delete
  - target "form"   → events: submit
  - target "order"  → events: create, update, delete, paid,
                              cancelled, shipped, payment_failed

Schemas document the matrix for LLM consumers but do NOT enforce
enum constraints — Voog rejects invalid combinations with 422 and
the vocabularies may extend.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site
from voog.projections import simplify_webhooks


def _validate_webhook_url(url: str, *, tool_name: str) -> str | None:
    """Reject URLs that aren't http:// or https://.

    Returns an error message string when the scheme is wrong, or ``None``
    when the URL is acceptable. Does NOT validate the rest of the URL —
    Voog enforces structure server-side (422).

    NOTE: Allows malformed but http(s)-prefixed URLs through (e.g.
    ``"http://"`` with no host); Voog enforces structure server-side.
    """
    # Caller is responsible for type — MCP schema upstream guarantees str | None.
    # RFC 3986 §3.1: schemes are case-insensitive.
    url_lower = url.lower()
    if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
        return f"{tool_name}: url must start with http:// or https:// (got {url!r})"
    return None


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="webhooks_list",
            description=(
                "List all webhooks on the site (id, enabled, target, "
                "event, url, target_id, description). Use the returned "
                "id for webhook_update / webhook_delete. Read-only."
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
            name="webhook_create",
            description=(
                "Create a webhook (POST /webhooks). Body is FLAT — no "
                "envelope wrapper. Required: target, event, url. "
                "Optional: enabled (default true), target_id, source "
                "(default 'api'), description. "
                "Voog target+event matrix: target='ticket' → "
                "create/update/delete; target='form' → submit; "
                "target='order' → create/update/delete/paid/cancelled/"
                "shipped/payment_failed. Voog returns 422 for invalid "
                "combinations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "target": {
                        "type": "string",
                        "minLength": 1,
                        "description": "ticket | form | order",
                    },
                    "event": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Event name; depends on target (see description)",
                    },
                    "url": {
                        "type": "string",
                        "minLength": 1,
                        "description": "HTTP(S) endpoint Voog calls when the event fires",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Whether the webhook fires (default true)",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "Optional id of the specific target object (e.g. order id)",
                    },
                    "source": {
                        "type": "string",
                        "description": "Origin marker — 'api' (default) or 'user'",
                    },
                    "description": {
                        "type": "string",
                        "description": "Free-text description (optional)",
                    },
                },
                "required": ["site", "target", "event", "url"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="webhook_update",
            description=(
                "Update a webhook (PUT /webhooks/{id}). Partial — supply "
                "ONLY the fields to change. Body is FLAT. At least one "
                "updatable field besides webhook_id is required."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "webhook_id": {
                        "type": "integer",
                        "description": "Voog webhook id (from webhooks_list)",
                    },
                    "target": {
                        "type": "string",
                        "minLength": 1,
                        "description": "ticket | form | order",
                    },
                    "event": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Event name; depends on target",
                    },
                    "url": {
                        "type": "string",
                        "minLength": 1,
                        "description": "HTTP(S) endpoint",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Whether the webhook fires",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "Optional id of the specific target object",
                    },
                    "source": {
                        "type": "string",
                        "description": "Origin marker — 'api' or 'user'",
                    },
                    "description": {
                        "type": "string",
                        "description": "Free-text description",
                    },
                },
                "required": ["site", "webhook_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="webhook_delete",
            description=(
                "Remove a webhook (DELETE /webhooks/{id}). Voog returns "
                "204. Requires force=true; without it the call is "
                "rejected. Run webhooks_list first to confirm the id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "webhook_id": {
                        "type": "integer",
                        "description": "Voog webhook id (from webhooks_list)",
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
                "required": ["site", "webhook_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _webhooks_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        webhooks = client.get_all("/webhooks")
        simplified = simplify_webhooks(webhooks)
        return success_response(simplified, summary=f"🪝 {len(simplified)} webhooks")
    except Exception as e:
        return error_response(f"webhooks_list failed: {e}")


_WEBHOOK_CREATE_FIELDS = (
    "target",
    "event",
    "url",
    "enabled",
    "target_id",
    "source",
    "description",
)


def _webhook_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    target = arguments.get("target") or ""
    event = arguments.get("event") or ""
    url = arguments.get("url") or ""
    if not target.strip():
        return error_response("webhook_create: target is required")
    if not event.strip():
        return error_response("webhook_create: event is required")
    if not url.strip():
        return error_response("webhook_create: url is required")
    err = _validate_webhook_url(url, tool_name="webhook_create")
    if err:
        return error_response(err)
    target_id = arguments.get("target_id")
    if target_id is not None:
        err = require_int("target_id", target_id, tool_name="webhook_create")
        if err:
            return error_response(err)

    body: dict = {}
    for key in _WEBHOOK_CREATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post("/webhooks", body)
        new_id = result.get("id") if isinstance(result, dict) else None
        return success_response(
            result,
            summary=(
                f"🪝 webhook created (id={new_id}, {target}/{event} → {url})"
                if new_id
                else f"🪝 webhook created ({target}/{event} → {url})"
            ),
        )
    except Exception as e:
        return error_response(f"webhook_create failed: {e}")


def _webhook_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    webhook_id = arguments.get("webhook_id")
    err = require_int("webhook_id", webhook_id, tool_name="webhook_update")
    if err:
        return error_response(err)
    url = arguments.get("url")
    if url is not None:
        err = _validate_webhook_url(url, tool_name="webhook_update")
        if err:
            return error_response(err)
    target_id = arguments.get("target_id")
    if target_id is not None:
        err = require_int("target_id", target_id, tool_name="webhook_update")
        if err:
            return error_response(err)
    body: dict = {}
    # Reuse the create field set — same surface, just partial.
    for key in _WEBHOOK_CREATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(
            "webhook_update: supply at least one of "
            f"{list(_WEBHOOK_CREATE_FIELDS)} besides webhook_id"
        )
    try:
        result = client.put(f"/webhooks/{webhook_id}", body)
        return success_response(
            result,
            summary=f"webhook {webhook_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"webhook_update id={webhook_id} failed: {e}")


def _webhook_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    webhook_id = arguments.get("webhook_id")
    err = require_int("webhook_id", webhook_id, tool_name="webhook_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="webhook_delete",
        target_desc=f"webhook {webhook_id}",
        hint="Run webhooks_list first to confirm.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/webhooks/{webhook_id}")
        return success_response(
            {"deleted": {"webhook_id": webhook_id}},
            summary=f"🗑️  webhook {webhook_id} deleted",
        )
    except Exception as e:
        return error_response(f"webhook_delete id={webhook_id} failed: {e}")


_DISPATCH = {
    "webhooks_list": _webhooks_list,
    "webhook_create": _webhook_create,
    "webhook_update": _webhook_update,
    "webhook_delete": _webhook_delete,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
