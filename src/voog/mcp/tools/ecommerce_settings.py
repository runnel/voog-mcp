"""MCP tools for Voog ecommerce store settings.

Two tools:
  - ``ecommerce_settings_get``    — GET /settings?include=translations
  - ``ecommerce_settings_update`` — PUT /settings {settings: {...}}

Most-asked-about field: per-language ``products_url_slug`` (e.g. EN
products serving under /en/tooted/... until per-lang slug is set —
project memory has the full story).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

# Voog ecommerce settings keys that are translatable per-language.
TRANSLATABLE_SETTINGS = frozenset(
    [
        "products_url_slug",
        "terms_url",
        "company_name",
        "bank_details",
    ]
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="ecommerce_settings_get",
            description=(
                "Get ecommerce store settings (currency, tax_rate, "
                "value_date_days, default_language, decimal_places, "
                "company_name, bank_details, terms, privacy_policy, "
                "products_url_slug, etc.). Includes per-language "
                "translations. Read-only."
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
                "translatable settings (products_url_slug, terms_url, "
                "company_name, bank_details). Wraps payload in {settings: "
                "{...}} envelope."
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


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

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
        for field in translations:
            if field not in TRANSLATABLE_SETTINGS:
                return error_response(
                    f"ecommerce_settings_update: translations field {field!r} "
                    f"not supported. Allowed: {sorted(TRANSLATABLE_SETTINGS)}"
                )
        body: dict = dict(attributes)
        if translations:
            body["translations"] = translations
        try:
            data = client.put(
                "/settings",
                {"settings": body},
                base=client.ecommerce_url,
            )
            return success_response(
                data,
                summary=f"ecommerce settings updated: {sorted(body.keys())}",
            )
        except Exception as e:
            return error_response(f"ecommerce_settings_update failed: {e}")

    return error_response(f"Unknown tool: {name}")
