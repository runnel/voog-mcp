"""MCP tools for Voog ecommerce products (list, get, update).

Three tools — all use ``client.ecommerce_url`` as base:

  - ``products_list``   — read-only, returns simplified projection of all
                           products (id, name, slug, sku, status, prices,
                           translations, updated_at)
  - ``product_get``     — read-only, returns full product detail with
                           variant_types + translations
  - ``product_update``  — mutating, updates translation-keyed fields
                           (name, slug per-language) — reversible by
                           calling again with previous values

Mirrors :mod:`voog_mcp.tools.layouts` pattern: explicit MCP annotation
triples on every tool, defensive validation, ``success_response``/``error_response``.

The list view's curated projection lives in :mod:`voog_mcp.projections`
(:func:`simplify_products`) and is shared with :mod:`voog_mcp.resources.products`
so the ``products_list`` tool and the ``voog://products`` resource produce the
same shape — consistent UX, and the shape can't drift between the two surfaces.

`product_set_images` is deferred (filesystem-touching: needs absolute paths
and the 3-step asset upload protocol). The ``voog.py`` CLI shim still works
for that operation.
"""
from mcp.types import CallToolResult, TextContent, Tool

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response
from voog_mcp.projections import (
    PRODUCTS_DETAIL_INCLUDE,
    PRODUCTS_LIST_INCLUDE,
    simplify_products,
)


# Voog API supports translation-keyed updates only on these fields. Each
# entry in `fields` (the input arg) must be `<field>-<lang>`, e.g. `name-et`,
# `slug-en`. Other fields (status, price, sku, ...) need a different payload
# shape and are out of scope for v1.
TRANSLATABLE_FIELDS = ("name", "slug")


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="products_list",
            description=(
                "List all ecommerce products on the Voog site (simplified: id, "
                "name, slug, sku, status, in_stock, on_sale, price, "
                "effective_price, translations, updated_at). Read-only. Same "
                "shape as the voog://products resource — consistent across the "
                "tools and resources surfaces."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="product_get",
            description=(
                "Get full product details by id, including variant_types and "
                "translations (?include=variant_types,translations). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "Voog product id"},
                },
                "required": ["product_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="product_update",
            description=(
                "Update product translation-keyed fields (name, slug per "
                "language). Pass `fields` as a flat object with keys like "
                "`name-et`, `slug-en`, `name-en`. The handler builds the "
                "nested {translations: {name: {et: ...}, slug: {en: ...}}} "
                "API payload. Reversible (call again with previous values), "
                "idempotent (same fields twice = same end state). For non-"
                "translation fields (status, price, sku) use the Voog admin "
                "UI or the voog.py CLI for now."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "Voog product id"},
                    "fields": {
                        "type": "object",
                        "description": (
                            "Flat field-language map. Keys must match "
                            "`<field>-<lang>` where field ∈ {name, slug}. "
                            "Example: {\"name-et\": \"Trippelgänger\", "
                            "\"slug-en\": \"trippelganger\"}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["product_id", "fields"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}

    if name == "products_list":
        return _products_list(client)

    if name == "product_get":
        return _product_get(arguments, client)

    if name == "product_update":
        return _product_update(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _products_list(client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        products = client.get_all(
            "/products",
            base=client.ecommerce_url,
            params={"include": PRODUCTS_LIST_INCLUDE},
        )
        simplified = simplify_products(products)
        return success_response(simplified, summary=f"🛒 {len(simplified)} products")
    except Exception as e:
        return error_response(f"products_list ebaõnnestus: {e}")


def _product_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    try:
        product = client.get(
            f"/products/{product_id}",
            base=client.ecommerce_url,
            params={"include": PRODUCTS_DETAIL_INCLUDE},
        )
        return success_response(product)
    except Exception as e:
        return error_response(f"product_get id={product_id} ebaõnnestus: {e}")


def _product_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    fields = arguments.get("fields") or {}
    if not fields:
        return error_response("product_update: fields must be a non-empty object")

    # Build nested translations payload from flat field-lang keys.
    translations: dict = {}
    for key, value in fields.items():
        if "-" not in key:
            return error_response(
                f"product_update: field {key!r} must use 'field-lang' format "
                f"(e.g. 'name-et', 'slug-en')"
            )
        field, lang = key.split("-", 1)
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: field {field!r} not supported. "
                f"Allowed: {TRANSLATABLE_FIELDS}"
            )
        # Reject malformed lang segment: empty (e.g. 'name-') or starts with
        # '-' (e.g. 'name--et' splits to lang='-et'). Voog would reject these
        # eventually with a generic 422; catching them here gives the caller
        # a precise error message.
        if not lang or lang.startswith("-"):
            return error_response(
                f"product_update: lang segment in {key!r} is empty or malformed"
            )
        if not value:
            return error_response(
                f"product_update: empty value for {key!r} (Voog rejects empty translations)"
            )
        translations.setdefault(field, {})[lang] = value

    payload = {"product": {"translations": translations}}

    try:
        result = client.put(
            f"/products/{product_id}",
            payload,
            base=client.ecommerce_url,
        )
        # Build a concise summary listing what changed
        changes = ", ".join(f"{k}-{lang}" for k, langs in translations.items() for lang in langs)
        return success_response(
            result,
            summary=f"✓ product {product_id} updated: {changes}",
        )
    except Exception as e:
        return error_response(f"product_update id={product_id} ebaõnnestus: {e}")
