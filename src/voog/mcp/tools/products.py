"""MCP tools for Voog ecommerce products (list, get, update).

Three tools — all use ``client.ecommerce_url`` as base:

  - ``products_list``   — read-only, returns simplified projection of all
                           products (id, name, slug, sku, status, prices,
                           translations, updated_at)
  - ``product_get``     — read-only, returns full product detail with
                           variant_types + translations
  - ``product_update``  — mutating, updates product fields via the full
                           ``{"product": {...}}`` envelope. Accepts three
                           combinable argument shapes: ``attributes`` (root-
                           level fields), ``translations`` (nested per-lang),
                           and ``fields`` (legacy v1.1 flat shape kept for
                           back-compat). Reversible by calling again with
                           previous values; idempotent.

Mirrors :mod:`voog.mcp.tools.layouts` pattern: explicit MCP annotation
triples on every tool, defensive validation, ``success_response``/``error_response``.

The list view's curated projection lives in :mod:`voog.projections`
(:func:`simplify_products`) and is shared with :mod:`voog.mcp.resources.products`
so the ``products_list`` tool and the ``voog://products`` resource produce the
same shape — consistent UX, and the shape can't drift between the two surfaces.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import (
    PRODUCTS_DETAIL_INCLUDE,
    PRODUCTS_LIST_INCLUDE,
    simplify_products,
)

# Voog product PUT envelope: {"product": {...}}. Allowed keys at the root
# of the envelope. Whitelist instead of pass-through so typos surface as
# a clean error rather than a 422 round-trip.
ATTR_KEYS = frozenset(
    [
        "status",
        "price",
        "sale_price",
        "sku",
        "stock",
        "description",
        "category_ids",
        "image_id",
        "asset_ids",
        "physical_properties",
        "uses_variants",
        "variant_types",
        "variants",
    ]
)

# Translatable fields supported by Voog ecommerce. Keep aligned with
# voog/cli/commands/products.py.
TRANSLATABLE_FIELDS = frozenset(["name", "slug", "description"])

# product.status enum per Voog (HTTP 422 otherwise — see project memory).
VALID_STATUS = frozenset(["draft", "live"])


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
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                },
                "required": ["site"],
            },
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
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "product_id": {"type": "integer", "description": "Voog product id"},
                },
                "required": ["site", "product_id"],
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
                "Update a product. Three argument shapes (combinable):\n"
                "  - `attributes`: flat object of root-level product fields "
                "(status, price, sale_price, sku, stock, description, "
                "category_ids, image_id, asset_ids, physical_properties, "
                "uses_variants, variant_types, variants).\n"
                "  - `translations`: nested {field: {lang: value}} for "
                "translatable fields (name, slug, description). Each "
                "field-language pair must be non-empty.\n"
                "  - `fields` (legacy v1.1 shape): flat 'name-et', 'slug-en' "
                "keys — auto-routed to translations. Kept for back-compat.\n"
                "At least one of attributes/translations/fields must be "
                "non-empty. Validates status enum {'draft', 'live'} and "
                "rejects unknown attribute keys (catches typos before they "
                "round-trip to a 422). Reversible by calling with previous "
                "values; idempotent (same input twice = same end state)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "product_id": {"type": "integer"},
                    "attributes": {
                        "type": "object",
                        "description": (
                            "Root-level product fields. Allowed keys: "
                            "status, price, sale_price, sku, stock, "
                            "description, category_ids, image_id, "
                            "asset_ids, physical_properties, uses_variants, "
                            "variant_types, variants."
                        ),
                    },
                    "translations": {
                        "type": "object",
                        "description": (
                            "Nested {field: {lang: value}}. Allowed fields: "
                            "name, slug, description."
                        ),
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Legacy v1.1 shape: flat 'name-et', 'slug-en' "
                            "keys. Auto-routed to translations."
                        ),
                    },
                },
                "required": ["site", "product_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

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
        return error_response(f"products_list failed: {e}")


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
        return error_response(f"product_get id={product_id} failed: {e}")


def _product_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    attributes = arguments.get("attributes") or {}
    translations = arguments.get("translations") or {}
    legacy_fields = arguments.get("fields") or {}

    if not (attributes or translations or legacy_fields):
        return error_response(
            "product_update: at least one of `attributes`, `translations`, "
            "or `fields` must be a non-empty object"
        )

    # Validate attributes — whitelist + status enum.
    for key in attributes:
        if key not in ATTR_KEYS:
            return error_response(
                f"product_update: attribute {key!r} not supported. Allowed: {sorted(ATTR_KEYS)}"
            )
    if "status" in attributes and attributes["status"] not in VALID_STATUS:
        return error_response(
            f"product_update: status must be one of "
            f"{sorted(VALID_STATUS)} (got {attributes['status']!r})"
        )

    # Validate explicit translations.
    merged_translations: dict = {}
    for field, langs in translations.items():
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: translations field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        if not isinstance(langs, dict) or not langs:
            return error_response(
                f"product_update: translations[{field!r}] must be a "
                "non-empty object {lang: value}"
            )
        for lang, value in langs.items():
            if not lang or lang.startswith("-"):
                return error_response(
                    f"product_update: empty/malformed lang in translations[{field!r}]: {lang!r}"
                )
            if not value:
                return error_response(
                    f"product_update: empty value for translations[{field!r}][{lang!r}]"
                )
            merged_translations.setdefault(field, {})[lang] = value

    # Fold legacy `fields` ('name-et', 'slug-en') into translations.
    for key, value in legacy_fields.items():
        if "-" not in key:
            return error_response(
                f"product_update: legacy field {key!r} must use 'field-lang' "
                "format (e.g. 'name-et', 'slug-en')"
            )
        field, lang = key.split("-", 1)
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: legacy field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        if not lang or lang.startswith("-"):
            return error_response(f"product_update: lang segment in {key!r} is empty or malformed")
        if not value:
            return error_response(
                f"product_update: empty value for {key!r} (Voog rejects empty translations)"
            )
        merged_translations.setdefault(field, {})[lang] = value

    product_body: dict = dict(attributes)
    if merged_translations:
        product_body["translations"] = merged_translations

    payload = {"product": product_body}

    try:
        result = client.put(
            f"/products/{product_id}",
            payload,
            base=client.ecommerce_url,
        )
        changes = sorted(
            list(attributes.keys())
            + [f"{k}-{lang}" for k, langs in merged_translations.items() for lang in langs]
        )
        return success_response(
            result,
            summary=f"✓ product {product_id} updated: {', '.join(changes)}",
        )
    except Exception as e:
        return error_response(f"product_update id={product_id} failed: {e}")
