"""MCP tools for Voog ecommerce products (list, get, update, create).

Four tools — all use ``client.ecommerce_url`` as base:

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
  - ``product_create``  — mutating, creates a new product via POST /products.
                           Requires name, slug, price. Same three argument
                           shapes as product_update. Uses POST's ``asset_ids``
                           envelope (not PUT's ``assets:[{id}]``).

Mirrors :mod:`voog.mcp.tools.layouts` pattern: explicit MCP annotation
triples on every tool, defensive validation, ``success_response``/``error_response``.

The list view's curated projection lives in :mod:`voog.projections`
(:func:`simplify_products`) and is shared with :mod:`voog.mcp.resources.products`
so the ``products_list`` tool and the ``voog://products`` resource produce the
same shape — consistent UX, and the shape can't drift between the two surfaces.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog._payloads import build_product_payload
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site, validate_translations_shape
from voog.projections import (
    PRODUCTS_DETAIL_INCLUDE,
    PRODUCTS_LIST_INCLUDE,
    simplify_products,
)

# Voog product PUT envelope: {"product": {...}}. Allowed keys at the root
# of the envelope. Whitelist instead of pass-through so typos surface as
# a clean error rather than a 422 round-trip.
#
# Two PUT-specific gotchas live in this whitelist (handled in
# ``_product_update`` below; see project memory
# ``feedback_voog_assets_vs_asset_ids`` and
# ``feedback_voog_variants_destructive_put``):
#
#   - ``asset_ids`` is the POST shape. On PUT the same field silently
#     keeps only the first/hero image. The tool translates it into the
#     PUT envelope ``assets:[{id:n}]`` internally before sending.
#   - ``variants`` without ``variant_attributes`` wipes ALL variants —
#     even ones with ``id``. The tool requires ``variant_attributes``
#     alongside, or explicit ``force=true``.
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
        "variant_attributes",
    ]
)

# Translatable fields supported by Voog ecommerce. Keep aligned with
# voog/cli/commands/products.py.
TRANSLATABLE_FIELDS = frozenset(["name", "slug", "description"])

# product.status enum per Voog (HTTP 422 otherwise — see project memory).
VALID_STATUS = frozenset(["draft", "live"])

# POST /products allowed root-level attributes. Differs from ATTR_KEYS
# (PUT-only) — POST permits direct `name`/`slug`/`price` (PUT prefers
# translations for name/slug). `asset_ids` is POST's image envelope; on
# PUT it's `assets:[{id}]`.
CREATE_ATTR_KEYS = frozenset(
    {
        "name",
        "slug",
        "price",
        "sale_price",
        "status",
        "description",
        "sku",
        "stock",
        "reserved_quantity",
        "category_ids",
        "image_id",
        "asset_ids",
        "physical_properties",
        "uses_variants",
        "variant_types",
    }
)

# Required by the Voog API on POST. Validation rejects payloads missing
# any of these (pre-empts a 422 round-trip). `name` and `slug` may also
# be supplied via translations/legacy fields; `price` only via attributes.
CREATE_REQUIRED_KEYS = ("name", "slug", "price")


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="products_list",
            description=(
                "List all ecommerce products on the Voog site (simplified: id, "
                "name, slug, sku, status, in_stock, on_sale, price, "
                "effective_price, stock, reserved_quantity, uses_variants, "
                "variants_count, translations, created_at, updated_at). "
                "Read-only. Same shape as the voog://products resource — "
                "consistent across the tools and resources surfaces. For "
                "per-variant stock on a variant-bearing product, follow up "
                "with product_get."
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
                "Get full product details by id, including the per-variant "
                "`variants` array (with stock, reserved_quantity, "
                "variant_attributes_text), `variant_types` definitions, and "
                "`translations` (?include=variants,variant_types,translations). "
                "Read-only."
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
                "uses_variants, variant_types, variants, variant_attributes). "
                "Note: asset_ids accepted; on PUT it's translated to the "
                "`assets:[{id}]` envelope Voog requires (sending raw "
                "asset_ids on PUT silently keeps only the hero image). "
                "`variants` without `variant_attributes` wipes ALL variants "
                "(even ones with `id`); pass both together, or set "
                "`force=true` to acknowledge.\n"
                "  - `translations`: nested {field: {lang: value}} for "
                "translatable fields (name, slug, description). Each "
                "field-language pair must be non-empty. Cannot overlap "
                "with attributes (e.g. attributes.description + "
                "translations.description in the same call is rejected).\n"
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
                            "variant_types, variants, variant_attributes. "
                            "asset_ids accepted; on PUT it's translated to "
                            "the `assets:[{id}]` envelope Voog requires."
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
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Required to send `variants` without "
                            "`variant_attributes` — Voog wipes all "
                            "variants in that case. Default false."
                        ),
                        "default": False,
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
        Tool(
            name="product_create",
            description=(
                "Create a new product (POST /products on ecommerce v1). "
                "Required: name, slug, price (Voog rejects POST without "
                "these). Three argument shapes (combinable):\n"
                "  - `attributes`: flat object of root-level product "
                "fields. Allowed keys: name, slug, price, sale_price, "
                "status, description, sku, stock, reserved_quantity, "
                "category_ids, image_id, asset_ids, physical_properties, "
                "uses_variants, variant_types. Note: POST uses `asset_ids` "
                "(list of int), unlike PUT which uses `assets:[{id}]`.\n"
                "  - `translations`: nested {field: {lang: value}} for "
                "translatable fields (name, slug, description). Each "
                "field-language pair must be non-empty.\n"
                "  - `fields` (legacy v1.1 shape): flat 'name-et', "
                "'slug-en' keys — auto-routed to translations.\n"
                "Validates status enum {'draft', 'live'} and rejects "
                "unknown attribute keys. The POST result includes the "
                "newly assigned product id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "attributes": {
                        "type": "object",
                        "description": (
                            "Root-level product fields. Required (in this "
                            "or in `translations`/`fields`): name, slug, price."
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
                            "Legacy v1.1 shape: 'name-et', 'slug-en' "
                            "keys. Auto-routed to translations."
                        ),
                    },
                },
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
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

    if name == "product_create":
        return _product_create(arguments, client)

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
    force = bool(arguments.get("force"))

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

    # Voog gotcha: PUT /products/{id} with `variants` but no
    # `variant_attributes` wipes ALL variants — even ones with `id`.
    # Require both, or an explicit force=true to acknowledge.
    if "variants" in attributes and not attributes.get("variant_attributes") and not force:
        return error_response(
            "product_update: passing `variants` without `variant_attributes` "
            "wipes ALL existing variants on PUT (Voog gotcha — even variants "
            "with `id` are dropped). Pass `variant_attributes` alongside, or "
            "set `force=true` to acknowledge the destructive default."
        )

    # Validate explicit translations.
    merged_translations: dict = {}
    for field, langs in translations.items():
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: translations field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        shape_err = validate_translations_shape(field, langs, tool_name="product_update")
        if shape_err:
            return error_response(shape_err)
        for lang, value in langs.items():
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

    # Reject `attributes` ∩ translations field overlap (covers BOTH the
    # explicit `translations` arg AND the legacy `fields` shape, which
    # was folded into merged_translations above). Sending the same field
    # via two surfaces in one envelope produces undefined behaviour —
    # per-language values can be silently clobbered. Today `description`
    # is the only field present in both whitelists.
    overlap = sorted(set(attributes) & TRANSLATABLE_FIELDS & set(merged_translations))
    if overlap:
        return error_response(
            f"product_update: field(s) {overlap} given in both `attributes` "
            "and translations (`translations` or legacy `fields`) — Voog's "
            "envelope is undefined when both are sent together. Pick one "
            "surface per field."
        )

    product_body: dict = dict(attributes)

    # Voog gotcha: PUT envelope is `assets:[{id:n}]`, not `asset_ids`
    # (that's POST-only). Sending `asset_ids` on PUT silently keeps only
    # the first/hero image. Translate internally so callers can keep
    # using the friendlier `asset_ids` shape.
    if "asset_ids" in product_body:
        asset_ids = product_body.pop("asset_ids")
        if not isinstance(asset_ids, list):
            return error_response(
                f"product_update: asset_ids must be a list of integers "
                f"(got {type(asset_ids).__name__})"
            )
        try:
            product_body["assets"] = [{"id": int(n)} for n in asset_ids]
        except (TypeError, ValueError) as e:
            return error_response(f"product_update: asset_ids items must be integers ({e})")

    if merged_translations:
        product_body["translations"] = merged_translations

    payload = build_product_payload(product_body)

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


def _product_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    attributes = arguments.get("attributes") or {}
    translations = arguments.get("translations") or {}
    legacy_fields = arguments.get("fields") or {}

    if not (attributes or translations or legacy_fields):
        return error_response(
            "product_create: at least one of `attributes`, `translations`, "
            "or `fields` must be a non-empty object"
        )

    # Whitelist + status enum (mirror product_update validation surface).
    for key in attributes:
        if key not in CREATE_ATTR_KEYS:
            return error_response(
                f"product_create: attribute {key!r} not supported. Allowed: {sorted(CREATE_ATTR_KEYS)}"
            )
    if "status" in attributes and attributes["status"] not in VALID_STATUS:
        return error_response(
            f"product_create: status must be one of "
            f"{sorted(VALID_STATUS)} (got {attributes['status']!r})"
        )

    # Validate explicit translations.
    merged_translations: dict = {}
    for field, langs in translations.items():
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_create: translations field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        shape_err = validate_translations_shape(field, langs, tool_name="product_create")
        if shape_err:
            return error_response(shape_err)
        for lang, value in langs.items():
            merged_translations.setdefault(field, {})[lang] = value

    # Fold legacy `fields` (e.g. 'name-et').
    for key, value in legacy_fields.items():
        if "-" not in key:
            return error_response(
                f"product_create: legacy field {key!r} must use 'field-lang' "
                "format (e.g. 'name-et', 'slug-en')"
            )
        field, lang = key.split("-", 1)
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_create: legacy field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        if not lang or lang.startswith("-"):
            return error_response(f"product_create: lang segment in {key!r} is empty or malformed")
        if not value:
            return error_response(
                f"product_create: empty value for {key!r} (Voog rejects empty translations)"
            )
        merged_translations.setdefault(field, {})[lang] = value

    # Required-fields check (POST contract). A required key may live
    # in `attributes` directly OR in `translations` (since name/slug
    # are translatable). For `price` only `attributes` is valid.
    for req in CREATE_REQUIRED_KEYS:
        if req == "price":
            if req not in attributes:
                return error_response(
                    f"product_create: required attribute {req!r} missing. "
                    "Voog requires `price` on POST."
                )
        else:
            if req in attributes:
                continue
            if req in merged_translations and merged_translations[req]:
                continue
            return error_response(
                f"product_create: required attribute {req!r} missing. "
                "Provide it via `attributes` or `translations`/`fields`."
            )

    # Reject `attributes` ∩ translations field overlap (mirrors the
    # `_product_update` guard). Sending the same field via two surfaces
    # in one POST envelope is ambiguous — Voog's API doc does not
    # specify which surface wins or whether they merge, so per-language
    # values can be silently clobbered. Pick one: set `name` directly
    # in attributes (treated as the default-language value alongside
    # `language_code`) OR via translations[name][lang], not both.
    overlap = sorted(set(attributes) & TRANSLATABLE_FIELDS & set(merged_translations))
    if overlap:
        return error_response(
            f"product_create: field(s) {overlap} given in both `attributes` "
            "and translations (`translations` or legacy `fields`) — Voog's "
            "POST envelope is undefined when both are sent together. Pick "
            "one surface per field."
        )

    product_body: dict = dict(attributes)

    # POST-specific: asset_ids stays as `asset_ids` (list of int).
    # No envelope translation — that's PUT's job.
    if "asset_ids" in product_body:
        if not isinstance(product_body["asset_ids"], list):
            return error_response(
                f"product_create: asset_ids must be a list of integers "
                f"(got {type(product_body['asset_ids']).__name__})"
            )
        try:
            product_body["asset_ids"] = [int(n) for n in product_body["asset_ids"]]
        except (TypeError, ValueError) as e:
            return error_response(f"product_create: asset_ids items must be integers ({e})")

    if merged_translations:
        product_body["translations"] = merged_translations

    payload = build_product_payload(product_body)

    try:
        result = client.post(
            "/products",
            payload,
            base=client.ecommerce_url,
        )
        new_id = result.get("id") if isinstance(result, dict) else None
        return success_response(
            result,
            summary=f"product created (id={new_id})" if new_id else "product created",
        )
    except Exception as e:
        return error_response(f"product_create failed: {e}")
