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
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_webhooks


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


_DISPATCH = {
    "webhooks_list": _webhooks_list,
    "webhook_create": _webhook_create,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
