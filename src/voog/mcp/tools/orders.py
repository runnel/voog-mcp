"""MCP tools for Voog ecommerce orders (read-only).

Two tools:
  - ``orders_list``  — GET /admin/api/ecommerce/v1/orders with optional
                       status / payment_status / created_after /
                       created_before filters.
  - ``order_get``    — GET /admin/api/ecommerce/v1/orders/{id}.

Both accept ``include_pii=false`` (default). When false, the response is
filtered through :func:`voog._payloads.redact_pii` which keeps a
whitelist of known-safe fields (id, code, uuid, status, payment_status,
shipping_status, all *amount fields, items[] inner-whitelist,
shipping_method inner-whitelist, cart_rules_applied,
custom_field_metadata, ...) and drops customer / billing_address /
shipping_address / note / return_url / urls /
gateway_transaction_id / shipping_method_option / external_shipment_attrs
/ custom_field_values.

Whitelist over blacklist (N4): if Voog adds a new PII field server-side
the whitelist drops it by default; a blacklist would silently leak.

Mutations stay passthrough via ``voog_ecommerce_api_call`` — order
updates carry finance / operations risk that needs a separate design
pass.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog._payloads import redact_pii
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_int, strip_site


def _include_pii_force_required(tool_name: str) -> str:
    """H2 (v1.4 review): consistent error message when ``include_pii=true``
    is passed without ``force=true``. PII access requires acknowledgement
    so an LLM that gets prompt-injected into setting ``include_pii=true``
    is still gated by the operator-facing MCP-host approval. SECURITY.md
    documents the threat model.
    """
    return (
        f"{tool_name}: include_pii=true requires force=true (PII-access "
        "acknowledgement gate; see SECURITY.md). MCP hosts surface force "
        "as a destructive-action approval, ensuring the operator sees the "
        "PII-exposure request before it goes through. CLI callers should "
        "pass --force alongside --include-pii."
    )


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="orders_list",
            description=(
                "List ecommerce orders (GET /admin/api/ecommerce/v1/orders). "
                "Read-only. Optional filters: status (e.g. 'created', "
                "'cancelled'), payment_status (e.g. 'paid', 'unpaid'), "
                "created_after (ISO8601), created_before (ISO8601). "
                "include_pii=false (default) strips customer email / name / "
                "address / phone / IP via whitelist. "
                "include_pii=true REQUIRES force=true alongside — passing "
                "include_pii=true without force=true is rejected (LLM-side "
                "PII-exfiltration gate; see SECURITY.md). MCP hosts surface "
                "force as a destructive-hint approval, which keeps "
                "prompt-injected `include_pii=true` calls behind operator "
                "consent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "status": {
                        "type": "string",
                        "description": "Order status filter (q.order.status.$eq).",
                    },
                    "payment_status": {
                        "type": "string",
                        "description": ("Payment status filter (q.order.payment_status.$eq)."),
                    },
                    "created_after": {
                        "type": "string",
                        "description": (
                            "ISO8601 timestamp; orders created at or after "
                            "(q.order.created_at.$gteq)."
                        ),
                    },
                    "created_before": {
                        "type": "string",
                        "description": (
                            "ISO8601 timestamp; orders created at or before "
                            "(q.order.created_at.$lteq)."
                        ),
                    },
                    "include_pii": {
                        "type": "boolean",
                        "description": (
                            "Default false (strips PII via whitelist). Set "
                            "true to keep customer email / name / address / "
                            "phone in the response. Requires force=true."
                        ),
                        "default": False,
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Required when include_pii=true (PII-access "
                            "acknowledgement gate; see SECURITY.md). "
                            "Ignored otherwise. Default false."
                        ),
                        "default": False,
                    },
                },
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                # destructiveHint=True when include_pii is requested:
                # MCP hosts surface a confirmation, gating prompt-injected
                # PII-exfiltration via include_pii=true.
                "destructiveHint": True,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="order_get",
            description=(
                "Get a single order by id (GET /admin/api/ecommerce/v1/"
                "orders/{id}). Read-only. include_pii=false (default) "
                "strips PII via whitelist. include_pii=true REQUIRES "
                "force=true alongside (LLM-side PII-exfiltration gate; "
                "see SECURITY.md)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "order_id": {"type": "integer"},
                    "include_pii": {
                        "type": "boolean",
                        "description": ("Default false (strips PII). Requires force=true."),
                        "default": False,
                    },
                    "force": {
                        "type": "boolean",
                        "description": ("Required when include_pii=true. Default false."),
                        "default": False,
                    },
                },
                "required": ["site", "order_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": True,
                "idempotentHint": True,
            },
        ),
    ]


def _orders_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    include_pii = bool(arguments.get("include_pii"))
    if include_pii and not arguments.get("force"):
        return error_response(_include_pii_force_required("orders_list"))

    params: dict = {}
    if arguments.get("status") is not None:
        params["q.order.status.$eq"] = arguments["status"]
    if arguments.get("payment_status") is not None:
        params["q.order.payment_status.$eq"] = arguments["payment_status"]
    if arguments.get("created_after") is not None:
        params["q.order.created_at.$gteq"] = arguments["created_after"]
    if arguments.get("created_before") is not None:
        params["q.order.created_at.$lteq"] = arguments["created_before"]

    try:
        orders = client.get_all("/orders", base=client.ecommerce_url, params=params or None)
        redacted = redact_pii(orders, include_pii=include_pii)
        suffix = "" if include_pii else " (PII stripped)"
        return success_response(redacted, summary=f"📦 {len(orders)} orders{suffix}")
    except Exception as e:
        return error_response(f"orders_list failed: {e}")


def _order_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    order_id = arguments.get("order_id")
    err = require_int("order_id", order_id, tool_name="order_get")
    if err:
        return error_response(err)
    include_pii = bool(arguments.get("include_pii"))
    if include_pii and not arguments.get("force"):
        return error_response(_include_pii_force_required("order_get"))
    try:
        order = client.get(f"/orders/{order_id}", base=client.ecommerce_url)
        redacted = redact_pii(order, include_pii=include_pii)
        suffix = "" if include_pii else " (PII stripped)"
        return success_response(redacted, summary=f"📦 order {order_id}{suffix}")
    except Exception as e:
        return error_response(f"order_get id={order_id} failed: {e}")


_DISPATCH = {
    "orders_list": _orders_list,
    "order_get": _order_get,
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
