"""MCP tools for Voog ecommerce discounts.

Five tools, all under ``client.ecommerce_url``:

  - ``discounts_list``    — GET /discounts
  - ``discount_get``      — GET /discounts/{id}
  - ``discount_create``   — POST /discounts  envelope {"discount": {...}}
  - ``discount_update``   — PUT /discounts/{id}  envelope {"discount": {...}}
  - ``discount_delete``   — DELETE /discounts/{id}  force-gated

Writable fields (verified by live capture + create probe 2026-05-27):
  code, name, description, amount, amount_mode, discount_type, status,
  applies_to, valid_from, valid_to, redemption_limit, stackable,
  currency.

Enum values that surfaced empirically (Voog rejects others with 422):
  status:        open | closed
  amount_mode:   net | percent (per real data: 'net')
  discount_type: percentage | fixed (per real data: 'percentage')
  applies_to:    cart | … (per real data: 'cart')

The plan's speculative ``active`` / ``percent`` / ``starts_at`` /
``ends_at`` / ``minimum_order_amount`` / ``max_uses`` /
``max_uses_per_customer`` field names do NOT match Voog. The real
field names (status / amount_mode / valid_from / valid_to /
redemption_limit) are used below.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site

DISCOUNT_FIELDS = (
    "code",
    "name",
    "description",
    "amount",
    "amount_mode",
    "discount_type",
    "status",
    "applies_to",
    "valid_from",
    "valid_to",
    "redemption_limit",
    "stackable",
    "currency",
)


def _build_discount_payload(body: dict) -> dict:
    return {"discount": dict(body)}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="discounts_list",
            description=(
                "List all ecommerce discounts (GET /admin/api/ecommerce/v1/discounts). Read-only."
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
            name="discount_get",
            description=(
                "Get a single discount by id (GET /admin/api/ecommerce/v1/"
                "discounts/{id}). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "discount_id": {"type": "integer"},
                },
                "required": ["site", "discount_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="discount_create",
            description=(
                "Create a discount (POST /admin/api/ecommerce/v1/discounts). "
                "Envelope {discount: {...}}. Required: code. Real enum "
                "values: status (open|closed), amount_mode (net|percent), "
                "discount_type (percentage|fixed), applies_to (cart|…). "
                "Optional: name, description, amount, valid_from "
                "(ISO8601), valid_to, redemption_limit, stackable, currency."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "code": {"type": "string", "minLength": 1},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "amount_mode": {"type": "string"},
                    "discount_type": {"type": "string"},
                    "status": {"type": "string"},
                    "applies_to": {"type": "string"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                    "redemption_limit": {"type": "integer"},
                    "stackable": {"type": "boolean"},
                    "currency": {"type": "string"},
                },
                "required": ["site", "code"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="discount_update",
            description=(
                "Update a discount (PUT /admin/api/ecommerce/v1/discounts/"
                "{id}). Envelope {discount: {...}}. Partial — at least one "
                "discount field must be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "discount_id": {"type": "integer"},
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "amount_mode": {"type": "string"},
                    "discount_type": {"type": "string"},
                    "status": {"type": "string"},
                    "applies_to": {"type": "string"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                    "redemption_limit": {"type": "integer"},
                    "stackable": {"type": "boolean"},
                    "currency": {"type": "string"},
                },
                "required": ["site", "discount_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="discount_delete",
            description=(
                "Delete a discount (DELETE /admin/api/ecommerce/v1/discounts/"
                "{id}). Requires force=true. Already-used discount records "
                "remain on past orders; deletion only prevents future use."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "discount_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "discount_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _discounts_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        rows = client.get_all("/discounts", base=client.ecommerce_url)
        return success_response(rows, summary=f"🏷  {len(rows)} discounts")
    except Exception as e:
        return error_response(f"discounts_list failed: {e}")


def _discount_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    discount_id = arguments.get("discount_id")
    err = require_int("discount_id", discount_id, tool_name="discount_get")
    if err:
        return error_response(err)
    try:
        row = client.get(f"/discounts/{discount_id}", base=client.ecommerce_url)
        return success_response(row)
    except Exception as e:
        return error_response(f"discount_get id={discount_id} failed: {e}")


def _discount_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    code = arguments.get("code") or ""
    if not isinstance(code, str) or not code.strip():
        return error_response("discount_create: code is required and non-empty")
    if arguments.get("redemption_limit") is not None:
        err = require_int(
            "redemption_limit",
            arguments["redemption_limit"],
            tool_name="discount_create",
        )
        if err:
            return error_response(err)
    body: dict = {}
    for key in DISCOUNT_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post(
            "/discounts",
            _build_discount_payload(body),
            base=client.ecommerce_url,
        )
        new_id = result.get("id") if isinstance(result, dict) else None
        summary = (
            f"🏷  discount created (id={new_id}, code={code!r})"
            if new_id
            else f"🏷  discount created (code={code!r})"
        )
        return success_response(result, summary=summary)
    except Exception as e:
        return error_response(f"discount_create failed: {e}")


def _discount_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    discount_id = arguments.get("discount_id")
    err = require_int("discount_id", discount_id, tool_name="discount_update")
    if err:
        return error_response(err)
    if arguments.get("redemption_limit") is not None:
        err = require_int(
            "redemption_limit",
            arguments["redemption_limit"],
            tool_name="discount_update",
        )
        if err:
            return error_response(err)
    body: dict = {}
    for key in DISCOUNT_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(f"discount_update: supply at least one of {list(DISCOUNT_FIELDS)}")
    try:
        result = client.put(
            f"/discounts/{discount_id}",
            _build_discount_payload(body),
            base=client.ecommerce_url,
        )
        return success_response(
            result,
            summary=f"🏷  discount {discount_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"discount_update id={discount_id} failed: {e}")


def _discount_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    discount_id = arguments.get("discount_id")
    err = require_int("discount_id", discount_id, tool_name="discount_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="discount_delete",
        target_desc=f"discount {discount_id}",
        hint="Already-used discount records remain on past orders.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/discounts/{discount_id}", base=client.ecommerce_url)
        return success_response(
            {"deleted": {"discount_id": discount_id}},
            summary=f"🗑️  discount {discount_id} deleted",
        )
    except Exception as e:
        return error_response(f"discount_delete id={discount_id} failed: {e}")


_DISPATCH = {
    "discounts_list": _discounts_list,
    "discount_get": _discount_get,
    "discount_create": _discount_create,
    "discount_update": _discount_update,
    "discount_delete": _discount_delete,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
