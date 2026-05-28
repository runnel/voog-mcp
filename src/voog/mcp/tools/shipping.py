"""MCP tools for Voog ecommerce shipping (read-only).

Two tools:
  - ``shipping_methods_list`` — GET /admin/api/ecommerce/v1/shipping_methods
  - ``gateways_list``         — GET /admin/api/ecommerce/v1/gateways

Read-only this phase: shipping method / gateway create/update/delete is
infrequent enough on Stella's day-to-day workload that wiring them
through ``voog_ecommerce_api_call`` passthrough is acceptable until a
future phase needs typed wrappers.

shipping_methods_list returns id, name, description, amount, tax_rate,
delivery_method, enabled, options[], created_at, updated_at — note that
``options`` for parcel-machine carriers (Omniva, SmartPost, etc.) is a
large nested list of pickup locations; expect 5KB+ responses per method.

gateways_list returns code, name, enabled, enabled_methods,
all_payment_methods, url, created_at, updated_at.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="shipping_methods_list",
            description=(
                "List all shipping methods (GET /admin/api/ecommerce/v1/"
                "shipping_methods). Read-only. Response includes the full "
                "options[] nested list for parcel-machine carriers (Omniva, "
                "SmartPost, …) — expect multi-KB payloads per method."
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
            name="gateways_list",
            description=(
                "List all payment gateways (GET /admin/api/ecommerce/v1/"
                "gateways). Read-only. Each entry has code, name, enabled, "
                "enabled_methods[], all_payment_methods[], url, "
                "created_at, updated_at."
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


def _shipping_methods_list(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    try:
        rows = client.get_all("/shipping_methods", base=client.ecommerce_url)
        return success_response(rows, summary=f"🚚 {len(rows)} shipping methods")
    except Exception as e:
        return error_response(f"shipping_methods_list failed: {e}")


def _gateways_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        rows = client.get_all("/gateways", base=client.ecommerce_url)
        return success_response(rows, summary=f"💳 {len(rows)} gateways")
    except Exception as e:
        return error_response(f"gateways_list failed: {e}")


_DISPATCH = {
    "shipping_methods_list": _shipping_methods_list,
    "gateways_list": _gateways_list,
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
