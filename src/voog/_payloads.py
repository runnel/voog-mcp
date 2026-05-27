"""Shared API payload builders.

Voog's API uses envelope wrappers (e.g. ``{"redirect_rule": {...}}``) and
field naming that occasionally surprises (``destination`` not ``target``).
Centralizing the payload-build keeps CLI and MCP from drifting when Voog
changes its schema.
"""

from __future__ import annotations


def build_product_payload(body: dict) -> dict:
    """Wrap a prepared product body in the Voog ``{"product": {...}}`` envelope.

    The caller (``_product_update``, ``_product_create``, CLI ``product``)
    is responsible for assembling and validating ``body`` — running the
    whitelist check, status enum check, asset_ids translation,
    translations folding, etc. This builder is a pure wrapper that
    centralises the envelope shape so future Voog API changes only need
    to touch one place.

    Voog accepts ``{"product": {...}}`` for both POST and PUT.
    """
    return {"product": dict(body)}


def build_settings_payload(body: dict) -> dict:
    """Wrap a prepared ecommerce settings body in the ``{"settings": {...}}`` envelope.

    Mirror of ``build_product_payload``: the caller assembles and
    validates ``body`` (attribute whitelist, translations shape, etc.);
    this builder just wraps it in the API envelope.

    Voog ecommerce v1 accepts ``{"settings": {...}}`` for PUT /settings.
    Centralising the envelope here means future Voog schema changes only
    need to touch one place rather than every caller of `_settings`.
    """
    return {"settings": dict(body)}


def build_redirect_payload(
    source: str,
    destination: str,
    *,
    redirect_type: int = 301,
    active: bool = True,
    regexp: bool = False,
) -> dict:
    """Build the payload for POST /redirect_rules.

    ``regexp`` toggles whether ``source`` is treated as a regex pattern.
    Voog's default for new rules is ``False`` (literal path match);
    callers opt in for pattern-based redirects.

    Use ``build_redirect_envelope`` for the "I already have a prepared
    body" case (e.g. PUT /redirect_rules/{id} with a merged dict).
    """
    return {
        "redirect_rule": {
            "source": source,
            "destination": destination,
            "redirect_type": redirect_type,
            "active": active,
            "regexp": regexp,
        }
    }


def build_redirect_envelope(body: dict) -> dict:
    """Wrap a prepared redirect_rule body in the ``{"redirect_rule": {...}}`` envelope.

    Mirror of ``build_product_payload`` / ``build_settings_payload``:
    the caller assembles ``body`` (e.g. via the GET-merge-PUT path in
    ``_redirect_update``) and this builder just wraps it. Use this when
    you already have the full dict; for fresh rule construction with
    keyword args, prefer ``build_redirect_payload``.
    """
    return {"redirect_rule": dict(body)}


# Article field mapping. The three autosaved_* keys are the writable
# pair to article.title/body/excerpt (read-only on the public side per
# Voog convention). Pass-through keys are non-autosaved, written
# directly to article.<field>.
_ARTICLE_AUTOSAVED_MAP = {
    "title": "autosaved_title",
    "body": "autosaved_body",
    "excerpt": "autosaved_excerpt",
}
_ARTICLE_PASSTHROUGH = ("description", "path", "image_id", "tag_names", "data")


def build_article_payload(arguments: dict, *, include_publish: bool = False) -> dict:
    """Build the FLAT body for POST/PUT /articles.

    Articles use a flat body (no envelope wrapper) but require the
    autosaved_* convention: ``article.title`` is read-only, writes go
    to ``autosaved_title``. Same for ``body`` and ``excerpt``. Other
    fields (``description``, ``path``, ``image_id``, ``tag_names``,
    ``data``) are pass-through.

    Only keys that are explicitly present and not None are included —
    empty strings/lists ARE included. Missing keys are simply absent.

    ``include_publish=True`` (POST-only) maps the caller's truthy
    ``arguments["publish"]`` to ``"publishing": True``. ``publish=False``
    is treated as absence (no ``publishing`` key emitted).
    """
    body: dict = {}
    for arg_key, body_key in _ARTICLE_AUTOSAVED_MAP.items():
        if arguments.get(arg_key) is not None:
            body[body_key] = arguments[arg_key]
    for key in _ARTICLE_PASSTHROUGH:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if include_publish and arguments.get("publish"):
        body["publishing"] = True
    return body


# --- Order whitelist for PII redaction (Phase 4 N4) ---
#
# Built from a live Stella capture (tests/fixtures/ecommerce/order_get.json
# + orders_list.json). Whitelist over blacklist: if Voog adds a new PII
# field (vat_id, customer_ip, ...) the redactor drops it by default
# instead of silently leaking. Adding a key requires a code change and a
# test.
#
# Dropped (PII or sensitive): customer, billing_address, shipping_address,
# note, return_url, urls, gateway_transaction_id, shipping_method_option,
# external_shipment_attrs, custom_field_values.

ORDER_PUBLIC_FIELDS: frozenset[str] = frozenset(
    {
        # identifiers
        "id",
        "uuid",
        "code",
        # lifecycle
        "status",
        "payment_status",
        "shipping_status",
        # timestamps
        "created_at",
        "updated_at",
        "paid_at",
        "completed_at",
        "value_date",
        "issued_date",
        # currency + money
        "currency",
        "total_amount",
        "total_discount_amount",
        # items totals
        "items_subtotal_amount",
        "items_original_amount",
        "items_tax_amount",
        "item_amounts",
        "tax_amounts",
        # shipping totals
        "shipping_subtotal_amount",
        "shipping_total_amount",
        "shipping_original_amount",
        "shipping_original_total_amount",
        "shipping_tax_amount",
        "shipping_tax_rate",
        # payment refs
        "payment_method",
        "gateway_code",
        "gateway_name",
        # shipping refs (shipping_method walked per inner whitelist below)
        "shipping_method_id",
        "shipping_method",
        # line items (walked per inner whitelist below)
        "items",
        # cart rules (walked per inner whitelist when list of dicts)
        "cart_rules_applied",
        # custom fields - schema labels only, NOT values
        "custom_field_metadata",
    }
)

ORDER_ITEM_PUBLIC_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "kind",
        "status",
        "product_id",
        "price",
        "effective_price",
        "original_price",
        "quantity",
        "amount",
        "subtotal_amount",
        "tax_amount",
        "tax_rate",
        "has_item_discount",
        "product_name",
    }
)

# shipping_method is a small descriptive object; we keep enough to display
# the method but drop `option` (parcel machine label can reveal customer
# location).
ORDER_SHIPPING_METHOD_PUBLIC_FIELDS: frozenset[str] = frozenset(
    {"id", "name", "description", "amount", "tax_rate", "delivery_method"}
)

# Defensive inner whitelist for `cart_rules_applied[]` entries. Live
# Stella fixture has `false` (no rules applied); the field set is based
# on Voog's cart_rules entity shape so future rules-applied orders get
# walked rather than passed through verbatim.
ORDER_CART_RULE_APPLIED_PUBLIC_FIELDS: frozenset[str] = frozenset(
    {"id", "code", "name", "kind", "value", "applied_amount"}
)


def redact_pii(value, *, include_pii: bool = False):
    """Strip PII from a Voog order payload using a whitelist.

    `value` may be a single order dict, a list of order dicts, or None.
    Other shapes pass through unchanged.

    With `include_pii=True` returns `value` unchanged (operator escape
    hatch). With `include_pii=False` (default), every top-level key
    outside `ORDER_PUBLIC_FIELDS` is dropped, and nested containers
    (`items`, `shipping_method`, `cart_rules_applied`) are walked with
    their own inner whitelists.
    """
    if include_pii:
        return value
    if value is None:
        return None
    if isinstance(value, list):
        return [redact_pii(v, include_pii=False) for v in value]
    if not isinstance(value, dict):
        return value

    redacted: dict = {}
    for key, val in value.items():
        if key not in ORDER_PUBLIC_FIELDS:
            continue
        if key == "items" and isinstance(val, list):
            redacted[key] = [
                {k: v for k, v in item.items() if k in ORDER_ITEM_PUBLIC_FIELDS}
                if isinstance(item, dict)
                else item
                for item in val
            ]
        elif key == "shipping_method" and isinstance(val, dict):
            redacted[key] = {
                k: v for k, v in val.items() if k in ORDER_SHIPPING_METHOD_PUBLIC_FIELDS
            }
        elif key == "cart_rules_applied" and isinstance(val, list):
            redacted[key] = [
                {k: v for k, v in rule.items() if k in ORDER_CART_RULE_APPLIED_PUBLIC_FIELDS}
                if isinstance(rule, dict)
                else rule
                for rule in val
            ]
        else:
            redacted[key] = val
    return redacted
