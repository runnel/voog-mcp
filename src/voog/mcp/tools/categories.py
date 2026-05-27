"""MCP tools for Voog ecommerce categories.

Five tools, all under ``client.ecommerce_url`` (``/admin/api/ecommerce/v1``):

  - ``categories_list``  — GET /categories
  - ``category_get``     — GET /categories/{id}
  - ``category_create``  — POST /categories  envelope {"category": {...}}
  - ``category_update``  — PUT /categories/{id}  envelope {"category": {...}}
  - ``category_delete``  — DELETE /categories/{id}  force-gated

Voog category writable fields verified by live capture 2026-05-27:
``name``, ``slug``, ``parent_id``. The plan's speculative ``description``
and ``image_id`` are NOT supported (Voog silently drops them — they
don't appear in the response). The whitelist below rejects them with a
clear message instead of letting them silently disappear.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_force, require_int, strip_site

CATEGORY_FIELDS = ("name", "slug", "parent_id")


def _build_category_payload(body: dict) -> dict:
    return {"category": dict(body)}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="categories_list",
            description=(
                "List all ecommerce product categories (GET /admin/api/"
                "ecommerce/v1/categories). Read-only. Each entry has id, "
                "name, slug, parent_id, depth, created_at, updated_at. Use "
                "category.id from the results as the "
                "products_list(category_id=...) filter."
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
            name="category_get",
            description=(
                "Get a single category by id (GET /admin/api/ecommerce/v1/"
                "categories/{id}). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "category_id": {"type": "integer"},
                },
                "required": ["site", "category_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="category_create",
            description=(
                "Create a category (POST /admin/api/ecommerce/v1/categories). "
                "Envelope: {category: {...}}. Required: name. Optional: "
                "slug (auto-generated if omitted), parent_id (for sub-"
                "categories). NOTE: Voog does not support description / "
                "image_id on categories despite some doc pages suggesting "
                "otherwise — verified empirically 2026-05-27."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "name": {"type": "string", "minLength": 1},
                    "slug": {"type": "string"},
                    "parent_id": {"type": "integer"},
                },
                "required": ["site", "name"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="category_update",
            description=(
                "Update a category (PUT /admin/api/ecommerce/v1/categories/"
                "{id}). Envelope: {category: {...}}. Partial — at least one "
                "of name / slug / parent_id must be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "category_id": {"type": "integer"},
                    "name": {"type": "string", "minLength": 1},
                    "slug": {"type": "string"},
                    "parent_id": {"type": "integer"},
                },
                "required": ["site", "category_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="category_delete",
            description=(
                "Delete a category (DELETE /admin/api/ecommerce/v1/categories/"
                "{id}). Requires force=true. Products in the category are "
                "NOT deleted; they're orphaned from the category. Voog may "
                "reject if the category has child categories."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "category_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "category_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _categories_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        cats = client.get_all("/categories", base=client.ecommerce_url)
        return success_response(cats, summary=f"🗂  {len(cats)} categories")
    except Exception as e:
        return error_response(f"categories_list failed: {e}")


def _category_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    category_id = arguments.get("category_id")
    err = require_int("category_id", category_id, tool_name="category_get")
    if err:
        return error_response(err)
    try:
        cat = client.get(f"/categories/{category_id}", base=client.ecommerce_url)
        return success_response(cat)
    except Exception as e:
        return error_response(f"category_get id={category_id} failed: {e}")


def _category_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    name = arguments.get("name") or ""
    if not isinstance(name, str) or not name.strip():
        return error_response("category_create: name is required and non-empty")
    if arguments.get("parent_id") is not None:
        err = require_int("parent_id", arguments["parent_id"], tool_name="category_create")
        if err:
            return error_response(err)
    body: dict = {}
    for key in CATEGORY_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post(
            "/categories",
            _build_category_payload(body),
            base=client.ecommerce_url,
        )
        new_id = result.get("id") if isinstance(result, dict) else None
        summary = (
            f"🗂  category created (id={new_id}, name={name!r})"
            if new_id
            else f"🗂  category created (name={name!r})"
        )
        return success_response(result, summary=summary)
    except Exception as e:
        return error_response(f"category_create failed: {e}")


def _category_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    category_id = arguments.get("category_id")
    err = require_int("category_id", category_id, tool_name="category_update")
    if err:
        return error_response(err)
    if arguments.get("parent_id") is not None:
        err = require_int("parent_id", arguments["parent_id"], tool_name="category_update")
        if err:
            return error_response(err)
    body: dict = {}
    for key in CATEGORY_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(f"category_update: supply at least one of {list(CATEGORY_FIELDS)}")
    try:
        result = client.put(
            f"/categories/{category_id}",
            _build_category_payload(body),
            base=client.ecommerce_url,
        )
        return success_response(
            result,
            summary=f"🗂  category {category_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"category_update id={category_id} failed: {e}")


def _category_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    category_id = arguments.get("category_id")
    err = require_int("category_id", category_id, tool_name="category_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="category_delete",
        target_desc=f"category {category_id}",
        hint="Products in the category are orphaned, not deleted.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/categories/{category_id}", base=client.ecommerce_url)
        return success_response(
            {"deleted": {"category_id": category_id}},
            summary=f"🗑️  category {category_id} deleted",
        )
    except Exception as e:
        return error_response(f"category_delete id={category_id} failed: {e}")


_DISPATCH = {
    "categories_list": _categories_list,
    "category_get": _category_get,
    "category_create": _category_create,
    "category_update": _category_update,
    "category_delete": _category_delete,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
