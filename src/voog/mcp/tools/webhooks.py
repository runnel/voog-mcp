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
    ]


def _webhooks_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        webhooks = client.get_all("/webhooks")
        simplified = simplify_webhooks(webhooks)
        return success_response(simplified, summary=f"🪝 {len(simplified)} webhooks")
    except Exception as e:
        return error_response(f"webhooks_list failed: {e}")


_DISPATCH = {
    "webhooks_list": _webhooks_list,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
