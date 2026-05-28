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
from voog.mcp.tools._helpers import (
    require_force,
    require_int,
    strip_site,
    validate_translations_shape,
)
from voog.projections import (
    PRODUCTS_DETAIL_INCLUDE,
    PRODUCTS_LIST_INCLUDE,
    simplify_products,
)

# products_bulk_action: client-side soft cap on target_ids[] length.
# Empirically Voog accepts 1001 ids without complaint (Stella OLD probe
# 2026-05-27), but accepting unbounded lists is a foot-gun for an LLM
# that could pass a 100k-id list and hang a worker. 10000 is a few-X
# safety margin over verified-working sizes; raise if a real workload
# needs more.
_BULK_TARGET_IDS_SOFT_CAP = 10000


# products_bulk_action: Voog's documented bulk-update verbs for the
# `actions[].action` field. Whitelisted server-side; sending an unknown
# verb returns a 422 round-trip. Source: live capture 2026-05-27 + Voog
# docs page /developers/api/ecommerce/products.
BULK_ACTION_VERBS = frozenset(
    {
        "set",
        "increase_by_fixed",
        "decrease_by_fixed",
        "increase_by_percent",
        "decrease_by_percent",
        "round",
        "round_upwards",
        "round_downwards",
        "merge",
        "remove",
    }
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
                "with product_get. Pass `category_id` to filter to products "
                "in that category (maps to q.product.category_ids.$in)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "category_id": {
                        "type": "integer",
                        "description": (
                            "Filter to products in this category. Maps to "
                            "the Voog filter q.product.category_ids.$in. "
                            "Omit for all products."
                        ),
                    },
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
        Tool(
            name="product_delete",
            description=(
                "Delete a product (DELETE /admin/api/ecommerce/v1/products/"
                "{id}). IRREVERSIBLE — Voog does not retain deleted products. "
                "Requires force=true; without it the call is rejected to "
                "prevent accidental deletion. Run products_list or product_get "
                "first to confirm the id, and site_snapshot if the product "
                "might be needed later."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "product_id": {"type": "integer"},
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Must be true to actually perform the delete. "
                            "Defaults to false (defensive opt-in)."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "product_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="product_duplicate",
            description=(
                "Duplicate a product (POST /admin/api/ecommerce/v1/products/"
                "{id}/duplicate). The new product inherits status='draft' per "
                "Voog default — call product_update(status='live') after "
                "editing if the duplicate should be public. Returns the new "
                "product's full payload; summary surfaces new_id and new "
                "title for easy chaining into product_update."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "product_id": {"type": "integer"},
                },
                "required": ["site", "product_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="products_bulk_action",
            description=(
                "Apply the same actions to many products in one request "
                "(PUT /admin/api/ecommerce/v1/products). This is NOT per-row "
                "arbitrary updates — every product in target_ids receives "
                "every action in actions. For one-off varied edits use "
                "product_update.\n"
                "\n"
                "Request shape:\n"
                "  - actions: list of {target_field, action, value, "
                "source_field?}. Allowed action verbs: set, increase_by_fixed, "
                "decrease_by_fixed, increase_by_percent, decrease_by_percent, "
                "round, round_upwards, round_downwards, merge, remove.\n"
                "  - target_ids: list of integer product ids, OR the literal "
                "string 'all' to apply to every product on the site.\n"
                "\n"
                "Response: {counters: {processed, failed}, processed_ids, "
                "failed_ids}. Duplicate ids in target_ids are collapsed "
                "server-side. No empirical batch-size cap observed up to "
                "1001 ids (Stella, 2026-05-27); send what you need."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_field": {"type": "string", "minLength": 1},
                                "action": {
                                    "type": "string",
                                    "enum": sorted(BULK_ACTION_VERBS),
                                },
                                "value": {},
                                "source_field": {"type": "string"},
                            },
                            "required": ["target_field", "action"],
                        },
                        "description": (
                            "Each {target_field, action, value, source_field?}. "
                            "Same actions apply to every id in target_ids."
                        ),
                    },
                    "target_ids": {
                        "oneOf": [
                            {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": _BULK_TARGET_IDS_SOFT_CAP,
                                "items": {"type": "integer"},
                            },
                            {"type": "string", "enum": ["all"]},
                        ],
                        "description": (
                            "List of product ids (up to "
                            f"{_BULK_TARGET_IDS_SOFT_CAP}), or the literal "
                            "string 'all' to target every product on the "
                            "site. NOTE: target_ids='all' is high blast "
                            "radius and requires force=true."
                        ),
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Required when target_ids='all'. Ignored when "
                            "target_ids is a list of explicit ids — the "
                            "caller has already named the rows."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "actions", "target_ids"],
            },
            annotations={
                "readOnlyHint": False,
                # destructiveHint=True: with target_ids='all', this tool can
                # in one call set every product's status to draft (entire
                # shop offline), zero every stock, or strip every image.
                # MCP hosts should surface a confirmation prompt for this
                # tool regardless of which target_ids shape was passed —
                # the LLM-prompt-injection threat model from PR #125 review
                # applies (a malicious product description could trick an
                # LLM into a mass-mutation call).
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def _products_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    category_id = arguments.get("category_id")
    if category_id is not None:
        err = require_int("category_id", category_id, tool_name="products_list")
        if err:
            return error_response(err)
    params: dict = {"include": PRODUCTS_LIST_INCLUDE}
    if category_id is not None:
        params["q.product.category_ids.$in"] = category_id
    try:
        products = client.get_all(
            "/products",
            base=client.ecommerce_url,
            params=params,
        )
        simplified = simplify_products(products)
        return success_response(simplified, summary=f"🛒 {len(simplified)} products")
    except Exception as e:
        return error_response(f"products_list failed: {e}")


def _product_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    err = require_int("product_id", product_id, tool_name="product_get")
    if err:
        return error_response(err)
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
    err = require_int("product_id", product_id, tool_name="product_update")
    if err:
        return error_response(err)
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
    # stock is always a whole-unit integer — reject bools explicitly.
    if "stock" in attributes and isinstance(attributes["stock"], bool):
        return error_response(
            f"product_update: stock must be an integer"
            f" (got {type(attributes['stock']).__name__}: {attributes['stock']!r})"
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
        assets = []
        for i, n in enumerate(asset_ids):
            err = require_int(f"asset_ids[{i}]", n, tool_name="product_update")
            if err:
                return error_response(err)
            assets.append({"id": n})
        product_body["assets"] = assets

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
    # stock is always a whole-unit integer — reject bools explicitly.
    if "stock" in attributes and isinstance(attributes["stock"], bool):
        return error_response(
            f"product_create: stock must be an integer"
            f" (got {type(attributes['stock']).__name__}: {attributes['stock']!r})"
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
        validated_ids = []
        for i, n in enumerate(product_body["asset_ids"]):
            err = require_int(f"asset_ids[{i}]", n, tool_name="product_create")
            if err:
                return error_response(err)
            validated_ids.append(n)
        product_body["asset_ids"] = validated_ids

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


def _product_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    err = require_int("product_id", product_id, tool_name="product_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="product_delete",
        target_desc=f"product {product_id}",
        hint="Voog does not retain deleted products — consider running site_snapshot first.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/products/{product_id}", base=client.ecommerce_url)
        return success_response(
            {"deleted": {"product_id": product_id}},
            summary=f"🗑️  product {product_id} deleted",
        )
    except Exception as e:
        return error_response(f"product_delete id={product_id} failed: {e}")


def _product_duplicate(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    err = require_int("product_id", product_id, tool_name="product_duplicate")
    if err:
        return error_response(err)
    try:
        result = client.post(
            f"/products/{product_id}/duplicate",
            {},
            base=client.ecommerce_url,
        )
        new_id = result.get("id") if isinstance(result, dict) else None
        new_title = None
        if isinstance(result, dict):
            translations = result.get("translations") or {}
            if isinstance(translations, dict):
                for lang_block in translations.values():
                    if isinstance(lang_block, dict) and lang_block.get("name"):
                        new_title = lang_block["name"]
                        break
        summary = (
            f"📑 product {product_id} duplicated → id={new_id}"
            + (f", title={new_title!r}" if new_title else "")
            + " (status='draft' — call product_update(status='live') to publish)"
        )
        return success_response(result, summary=summary)
    except Exception as e:
        return error_response(f"product_duplicate id={product_id} failed: {e}")


def _products_bulk_action(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    actions = arguments.get("actions")
    target_ids = arguments.get("target_ids")

    # actions: non-empty list of dicts with valid verb.
    if not isinstance(actions, list) or not actions:
        return error_response(
            "products_bulk_action: actions must be a non-empty list of "
            "{target_field, action, value, source_field?} objects"
        )
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            return error_response(
                f"products_bulk_action: actions[{i}] must be an object "
                f"(got {type(action).__name__})"
            )
        if not action.get("target_field"):
            return error_response(f"products_bulk_action: actions[{i}] missing target_field")
        verb = action.get("action")
        if verb not in BULK_ACTION_VERBS:
            return error_response(
                f"products_bulk_action: actions[{i}].action must be one of "
                f"{sorted(BULK_ACTION_VERBS)} (got {verb!r})"
            )

    # target_ids: list of ints (no-bool) OR the literal string "all".
    if isinstance(target_ids, str):
        if target_ids != "all":
            return error_response(
                "products_bulk_action: target_ids string must be exactly "
                f"'all' (got {target_ids!r})"
            )
        # 'all' is high blast radius — every product on the site gets
        # every action. Force-gate it. Explicit-id lists stay no-force
        # because the caller has named the rows.
        if not arguments.get("force"):
            return error_response(
                "products_bulk_action: target_ids='all' requires force=true. "
                "This applies the action to every product on the site — set "
                "force=true after confirming the blast radius is intentional."
            )
    elif isinstance(target_ids, list):
        if not target_ids:
            return error_response("products_bulk_action: target_ids list must be non-empty")
        if len(target_ids) > _BULK_TARGET_IDS_SOFT_CAP:
            return error_response(
                f"products_bulk_action: target_ids has {len(target_ids)} "
                f"entries, max {_BULK_TARGET_IDS_SOFT_CAP}. Use "
                f"target_ids='all' (with force=true) for site-wide actions, "
                f"or split into smaller batches."
            )
        for j, tid in enumerate(target_ids):
            err = require_int(f"target_ids[{j}]", tid, tool_name="products_bulk_action")
            if err:
                return error_response(err)
    else:
        return error_response(
            "products_bulk_action: target_ids must be a list of ints or the string 'all'"
        )

    body = {"actions": list(actions), "target_ids": target_ids}
    try:
        result = client.put("/products", body, base=client.ecommerce_url)
    except Exception as e:
        return error_response(f"products_bulk_action failed: {e}")

    counters = result.get("counters", {}) if isinstance(result, dict) else {}
    processed = counters.get("processed", 0)
    failed = counters.get("failed", 0)
    verbs = ", ".join(a.get("action", "?") for a in actions)
    summary = f"🛒 bulk action [{verbs}]: {processed} processed, {failed} failed"
    return success_response(result, summary=summary)


_DISPATCH = {
    "products_list": _products_list,
    "product_get": _product_get,
    "product_update": _product_update,
    "product_create": _product_create,
    "product_delete": _product_delete,
    "product_duplicate": _product_duplicate,
    "products_bulk_action": _products_bulk_action,
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
