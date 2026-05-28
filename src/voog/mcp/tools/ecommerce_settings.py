"""MCP tools for Voog ecommerce store settings.

Two tools:
  - ``ecommerce_settings_get``    — GET /settings?include=translations
  - ``ecommerce_settings_update`` — PUT /settings {settings: {...}}

Most-asked-about field: per-language ``products_url_slug`` (e.g. EN
products serving under /en/tooted/... until per-lang slug is set —
project memory has the full story).

v1.4 (E11): translatable-key allowlist is discovered at runtime from
``GET /settings?include=translations`` rather than hardcoded. The
top-level ``translations`` map's keys are the language-agnostic
settings-key set (verified empirically against Stella's tenant —
see plan Task 1 R7); each key's value is ``{lang: text}``.

Cache contract:
  - Keyed on site identity (``client.host``). Per-language is NOT
    needed because ``translations.keys()`` is language-agnostic.
  - Per-token allowlist variance is assumed zero — translatable
    keys are settings-schema-level, not permission-level (verified
    empirically against Stella's tenant, 2026-05-28 R7 probe). If a
    future Voog feature ever gates allowlist keys per token, swap
    the cache key for a ``(host, token-hash)`` tuple.
  - 60-second TTL — new server-side keys picked up within 60s. No
    invalidation API; restart the MCP server if you need a faster
    refresh.
  - Cache hit logs at DEBUG level. Cache miss triggers a discovery
    GET (also logged at DEBUG).
  - Race condition: two concurrent calls on the same site can both
    miss the cache and both fetch. Acceptable — the GET is read-only
    and both results are identical. No lock added; the modest cost
    of an extra GET on a rare race is preferable to lock complexity
    in this code path.
  - On discovery-GET failure (network blip, Voog 5xx, etc.) the
    tool returns an error rather than falling back to a hardcoded
    set. The operator can retry; we don't want a silent PUT with a
    possibly-invalid translation key.
"""

import logging
import time

from mcp.types import CallToolResult, TextContent, Tool

from voog._payloads import build_settings_payload
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site, validate_translations_shape

logger = logging.getLogger(__name__)

# Cache: site host → (expires_at_monotonic, frozenset of translatable keys).
# Module-level; shared across MCP-tool invocations within one server
# process. Cleared on process restart.
_TRANSLATABLE_KEYS_CACHE: dict[str, tuple[float, frozenset[str]]] = {}

# TTL for discovered allowlist. 60s strikes a balance: new server-side
# keys are picked up within a minute, but a back-to-back fan-out of
# 10-20 settings updates (rare) doesn't pay for 10-20 discovery GETs.
_TRANSLATABLE_KEYS_TTL_SECONDS = 60.0


def _cache_key(client: VoogClient) -> str:
    """Cache key for the translatable-keys allowlist. Site host is the
    identity (one Voog tenant per host). ``client.host`` is the
    canonical identifier on :class:`voog.client.VoogClient`."""
    return getattr(client, "host", "") or ""


def _discover_translatable_keys(client: VoogClient) -> frozenset[str]:
    """Fetch the current translatable-keys allowlist from Voog. Caches
    for ``_TRANSLATABLE_KEYS_TTL_SECONDS`` per-site. Raises through to
    the caller on transport/parse failure — discovery cannot fall back
    to a hardcoded set, see module docstring."""
    key = _cache_key(client)
    now = time.monotonic()
    entry = _TRANSLATABLE_KEYS_CACHE.get(key)
    if entry is not None and entry[0] > now:
        logger.debug(
            "translatable-keys cache hit for site %r (%d keys, %.1fs left)",
            key,
            len(entry[1]),
            entry[0] - now,
        )
        return entry[1]

    logger.debug("translatable-keys cache miss for site %r — discovering", key)
    data = client.get(
        "/settings",
        base=client.ecommerce_url,
        params={"include": "translations"},
    )
    # ``data`` shape per R7: top-level ``translations`` is
    # {settings_key: {lang: text}}. Pull the outer keys.
    translations = data.get("translations") if isinstance(data, dict) else None
    if not isinstance(translations, dict):
        # Defensive — empty/absent translations on a fresh tenant. Treat
        # as "no translatable keys discovered yet" rather than raising —
        # the caller's translations dict will then be rejected with a
        # clear "field X not supported" error, which is the right UX.
        keys: frozenset[str] = frozenset()
    else:
        keys = frozenset(translations.keys())

    _TRANSLATABLE_KEYS_CACHE[key] = (now + _TRANSLATABLE_KEYS_TTL_SECONDS, keys)
    return keys


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="ecommerce_settings_get",
            description=(
                "Get ecommerce store settings (currency, tax_rate, "
                "value_date_days, default_language, decimal_places, "
                "company_name, bank_details, terms, privacy_policy, "
                "products_url_slug, etc.). Includes per-language "
                "translations. Read-only. "
                "Note: this is also the source of truth for "
                "`price_entry_mode` (net vs gross) — product tools' "
                "price fields are interpreted against this setting."
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
            name="ecommerce_settings_update",
            description=(
                "Update ecommerce settings. attributes: flat root-level "
                "fields (currency, tax_rate, notification_email, ...). "
                "translations: nested {field: {lang: value}} for "
                "translatable settings. The set of translatable keys is "
                "discovered at runtime from "
                "`GET /settings?include=translations` (cached 60s "
                "per-site) — new server-side keys are picked up "
                "automatically. Wraps payload in {settings: {...}} "
                "envelope."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "attributes": {"type": "object"},
                    "translations": {"type": "object"},
                },
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset({"ecommerce_settings_get", "ecommerce_settings_update"})


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")

    # S9: tag every HTTP request inside this dispatch with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring. Wrapping
    # the whole dispatch (rather than per-branch) is safe because the
    # ``name not in _KNOWN_TOOLS`` early-return above keeps with_tool
    # from entering with an unknown / typo'd tool name.
    with client.with_tool(name):
        if name == "ecommerce_settings_get":
            try:
                data = client.get(
                    "/settings",
                    base=client.ecommerce_url,
                    params={"include": "translations"},
                )
                return success_response(data)
            except Exception as e:
                return error_response(f"ecommerce_settings_get failed: {e}")

        if name == "ecommerce_settings_update":
            attributes = arguments.get("attributes") or {}
            translations = arguments.get("translations") or {}
            if not (attributes or translations):
                return error_response(
                    "ecommerce_settings_update: attributes or translations required"
                )

            # E11: discover translatable-keys allowlist at runtime. Cached
            # 60s per-site. Failure to discover surfaces as an error rather
            # than falling back to a guessed allowlist — see module docs.
            if translations:
                try:
                    allowed = _discover_translatable_keys(client)
                except Exception as e:
                    return error_response(
                        f"ecommerce_settings_update: could not discover translatable-keys "
                        f"allowlist via GET /settings?include=translations: {e}"
                    )

                for field, langs in translations.items():
                    if field not in allowed:
                        return error_response(
                            f"ecommerce_settings_update: translations field {field!r} "
                            f"not supported. Allowed (discovered at runtime): {sorted(allowed)}"
                        )
                    shape_err = validate_translations_shape(
                        field, langs, tool_name="ecommerce_settings_update"
                    )
                    if shape_err:
                        return error_response(shape_err)

            body: dict = dict(attributes)
            if translations:
                body["translations"] = translations
            try:
                data = client.put(
                    "/settings",
                    build_settings_payload(body),
                    base=client.ecommerce_url,
                )
                return success_response(
                    data,
                    summary=f"ecommerce settings updated: {sorted(body.keys())}",
                )
            except Exception as e:
                return error_response(f"ecommerce_settings_update failed: {e}")

    return error_response(f"Unknown tool: {name}")
