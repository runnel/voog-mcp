"""MCP tools for Voog ecommerce cart_rules.

Five tools, all under ``client.ecommerce_url``:

  - ``cart_rules_list``   — GET /cart_rules
  - ``cart_rule_get``     — GET /cart_rules/{id}
  - ``cart_rule_create``  — POST /cart_rules  envelope {"cart_rule": {...}}
  - ``cart_rule_update``  — PUT /cart_rules/{id}  envelope {"cart_rule": {...}}
  - ``cart_rule_delete``  — DELETE /cart_rules/{id}  force-gated

Writable fields verified empirically (Stella OLD, 2026-05-27):
  enabled, position, kind, target_id, target_kind, conditions, result,
  valid_from, valid_to.

Real shape of `conditions` (array) and `result` (dict):
  conditions: [{value, comparator, field, value_type}, ...]
  result:     {value, field, value_type}

Example cart_rule (Stella OLD, real data):
  {
    "kind": "shipping_cost",
    "target_kind": "shipping_method",
    "target_id": 9001,
    "conditions": [
      {"value": "125.0", "comparator": ">=",
       "field": "items_subtotal_amount", "value_type": "decimal"}
    ],
    "result": {"value": "0.0",
               "field": "shipping_subtotal_amount",
               "value_type": "decimal"}
  }

Inner shapes are pass-through (no inner key validation) — Voog returns
clear 422s for malformed conditions/result.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site

CART_RULE_FIELDS = (
    "enabled",
    "position",
    "kind",
    "target_id",
    "target_kind",
    "conditions",
    "result",
    "valid_from",
    "valid_to",
)


def _build_cart_rule_payload(body: dict) -> dict:
    return {"cart_rule": dict(body)}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="cart_rules_list",
            description=(
                "List all cart rules (GET /admin/api/ecommerce/v1/cart_rules). Read-only."
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
            name="cart_rule_get",
            description=(
                "Get a single cart rule by id (GET /admin/api/ecommerce/v1/"
                "cart_rules/{id}). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "cart_rule_id": {"type": "integer"},
                },
                "required": ["site", "cart_rule_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="cart_rule_create",
            description=(
                "Create a cart rule (POST /admin/api/ecommerce/v1/cart_rules). "
                "Envelope {cart_rule: {...}}. Required: kind, target_kind, "
                "target_id, conditions[], result{}. Inner conditions[] entries "
                "are {value, comparator, field, value_type}. result is "
                "{value, field, value_type}. Inner key validation is left to "
                "Voog — invalid combos return a 422 with the offending field."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "kind": {"type": "string", "minLength": 1},
                    "target_kind": {"type": "string", "minLength": 1},
                    "target_id": {"type": "integer"},
                    "conditions": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "result": {"type": "object"},
                    "enabled": {"type": "boolean"},
                    "position": {"type": "integer"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                },
                "required": [
                    "site",
                    "kind",
                    "target_kind",
                    "target_id",
                    "conditions",
                    "result",
                ],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="cart_rule_update",
            description=(
                "Update a cart rule (PUT /admin/api/ecommerce/v1/cart_rules/"
                "{id}). Envelope {cart_rule: {...}}. Partial — at least one "
                "field must be supplied. Common partial updates: enabled, "
                "position."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "cart_rule_id": {"type": "integer"},
                    "enabled": {"type": "boolean"},
                    "position": {"type": "integer"},
                    "kind": {"type": "string"},
                    "target_kind": {"type": "string"},
                    "target_id": {"type": "integer"},
                    "conditions": {"type": "array", "items": {"type": "object"}},
                    "result": {"type": "object"},
                    "valid_from": {"type": "string"},
                    "valid_to": {"type": "string"},
                },
                "required": ["site", "cart_rule_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="cart_rule_delete",
            description=(
                "Delete a cart rule (DELETE /admin/api/ecommerce/v1/"
                "cart_rules/{id}). Requires force=true. Past orders that "
                "already had the rule applied are not affected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "cart_rule_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "cart_rule_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _cart_rules_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        rows = client.get_all("/cart_rules", base=client.ecommerce_url)
        return success_response(rows, summary=f"📐 {len(rows)} cart rules")
    except Exception as e:
        return error_response(f"cart_rules_list failed: {e}")


def _cart_rule_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    cart_rule_id = arguments.get("cart_rule_id")
    err = require_int("cart_rule_id", cart_rule_id, tool_name="cart_rule_get")
    if err:
        return error_response(err)
    try:
        row = client.get(f"/cart_rules/{cart_rule_id}", base=client.ecommerce_url)
        return success_response(row)
    except Exception as e:
        return error_response(f"cart_rule_get id={cart_rule_id} failed: {e}")


def _cart_rule_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    for required in ("kind", "target_kind", "conditions", "result"):
        if arguments.get(required) in (None, "", []):
            return error_response(f"cart_rule_create: {required} is required and non-empty")
    if arguments.get("target_id") is not None:
        err = require_int("target_id", arguments["target_id"], tool_name="cart_rule_create")
        if err:
            return error_response(err)
    if not isinstance(arguments.get("conditions"), list):
        return error_response("cart_rule_create: conditions must be a list")
    if not isinstance(arguments.get("result"), dict):
        return error_response("cart_rule_create: result must be an object")
    body: dict = {}
    for key in CART_RULE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post(
            "/cart_rules",
            _build_cart_rule_payload(body),
            base=client.ecommerce_url,
        )
        new_id = result.get("id") if isinstance(result, dict) else None
        kind = body.get("kind", "?")
        summary = (
            f"📐 cart rule created (id={new_id}, kind={kind!r})"
            if new_id
            else f"📐 cart rule created (kind={kind!r})"
        )
        return success_response(result, summary=summary)
    except Exception as e:
        return error_response(f"cart_rule_create failed: {e}")


def _cart_rule_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    cart_rule_id = arguments.get("cart_rule_id")
    err = require_int("cart_rule_id", cart_rule_id, tool_name="cart_rule_update")
    if err:
        return error_response(err)
    if arguments.get("target_id") is not None:
        err = require_int("target_id", arguments["target_id"], tool_name="cart_rule_update")
        if err:
            return error_response(err)
    body: dict = {}
    for key in CART_RULE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(f"cart_rule_update: supply at least one of {list(CART_RULE_FIELDS)}")
    try:
        result = client.put(
            f"/cart_rules/{cart_rule_id}",
            _build_cart_rule_payload(body),
            base=client.ecommerce_url,
        )
        return success_response(
            result,
            summary=f"📐 cart rule {cart_rule_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"cart_rule_update id={cart_rule_id} failed: {e}")


def _cart_rule_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    cart_rule_id = arguments.get("cart_rule_id")
    err = require_int("cart_rule_id", cart_rule_id, tool_name="cart_rule_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="cart_rule_delete",
        target_desc=f"cart rule {cart_rule_id}",
        hint="Past orders that already had the rule applied are not affected.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/cart_rules/{cart_rule_id}", base=client.ecommerce_url)
        return success_response(
            {"deleted": {"cart_rule_id": cart_rule_id}},
            summary=f"🗑️  cart rule {cart_rule_id} deleted",
        )
    except Exception as e:
        return error_response(f"cart_rule_delete id={cart_rule_id} failed: {e}")


_DISPATCH = {
    "cart_rules_list": _cart_rules_list,
    "cart_rule_get": _cart_rule_get,
    "cart_rule_create": _cart_rule_create,
    "cart_rule_update": _cart_rule_update,
    "cart_rule_delete": _cart_rule_delete,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
