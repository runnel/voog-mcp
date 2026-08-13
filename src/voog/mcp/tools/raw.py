"""Generic Admin API + Ecommerce v1 API passthrough tools.

Four tools:

  - ``voog_admin_api_read``       — readOnlyHint=true GET-only passthrough
                                     for Admin API endpoints not yet covered
                                     by typed tools.
  - ``voog_ecommerce_api_read``   — readOnlyHint=true GET-only passthrough
                                     for Ecommerce v1 endpoints.
  - ``voog_admin_api_call``       — WRITE passthrough (POST/PUT/PATCH/
                                     DELETE). GET was removed in v1.5.
  - ``voog_ecommerce_api_call``   — write passthrough for Ecommerce v1.

The split closes S3 (v4 audit): conservative annotations
(``destructiveHint=True``) on the write-capable tools cause MCP hosts to
surface confirmation prompts on every call — for read-only traffic that's
alarm fatigue. The read tools advertise ``readOnlyHint=true`` and hosts can
skip the prompt.

GET removal (v1.5, announced in the v1.4 changelog): the ``*_call`` tools
no longer accept ``method='GET'``. Keeping it meant every read through the
generic surface carried ``destructiveHint=True`` and asked the operator to
approve a request that changes nothing — the alarm fatigue the split
existed to end. v1.4 shipped the deprecation on two channels
(``DeprecationWarning`` for the Python side, a ``DEPRECATED:`` response
prefix for the MCP side) for one release; the schema enum now omits GET and
the handler answers a GET with a migration message naming the ``*_read``
tool, because a conforming host rejects it at the schema and a
non-conforming one must not get a silent 405 from Voog instead.

Path validation rejects three obvious foot-guns:
  - Empty path or path without a leading ``/`` (would build an invalid URL).
  - Absolute URL (would let the caller bypass the configured host — a
    secret-exfiltration vector if the response is logged).
  - ``..`` segments (no legitimate Voog endpoint contains them; refusing
    them is cheap defence-in-depth).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import _decode_until_stable, strip_site

# Write methods accepted by the ``*_call`` passthrough tools. GET is
# deliberately absent as of v1.5 — see the module docstring.
ALLOWED_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Methods the ``*_call`` tools used to accept, mapped to the tool that
# replaced them. A caller that still sends one gets this instead of a
# generic "method must be one of …", because the useful information is
# *where the capability moved*, not what is left.
_REMOVED_METHOD_TARGET = {
    "GET": {
        "admin": "voog_admin_api_read",
        "ecommerce": "voog_ecommerce_api_read",
    }
}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="voog_admin_api_read",
            description=(
                "Read-only Admin API passthrough. Forward a GET request to "
                "https://<host>/admin/api<path>. Use this when no typed "
                "read tool covers the endpoint (forms, tickets, tags, "
                "media_sets, etc.). Read-only — MCP hosts may skip the "
                "destructive-action confirmation prompt."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. '/forms', '/articles/42'."
                        ),
                    },
                    "params": {
                        "type": ["object", "null"],
                        "description": (
                            "Optional query parameters as a flat string-keyed "
                            "object, e.g. {'include': 'translations'}."
                        ),
                    },
                },
                "required": ["site", "path"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="voog_ecommerce_api_read",
            description=(
                "Read-only Ecommerce v1 API passthrough. Forward a GET "
                "request to https://<host>/admin/api/ecommerce/v1<path>. "
                "Supports ?include=... and ?language_code=.... Read-only — "
                "MCP hosts may skip the destructive-action confirmation "
                "prompt."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. "
                            "'/orders', '/products/42', '/settings'."
                        ),
                    },
                    "params": {
                        "type": ["object", "null"],
                        "description": ("Optional query parameters as a flat string-keyed object."),
                    },
                },
                "required": ["site", "path"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="voog_admin_api_call",
            description=(
                "Generic Admin API WRITE passthrough. Forward an HTTP request "
                "to https://<host>/admin/api<path> using the configured site's "
                "API token. method ∈ {POST, PUT, PATCH, DELETE}; body "
                "is JSON-serialised on POST/PUT/PATCH. Use this when no typed "
                "tool covers the endpoint (orders, forms, tickets, elements, "
                "tags, media_sets, webhooks, etc.). Conservative annotations "
                "(destructiveHint=true) — Claude will confirm before calling.\n"
                "\n"
                "For READS use voog_admin_api_read — this tool no longer "
                "accepts method='GET' (removed in v1.5).\n"
                "\n"
                "⚠️ `PUT /media_sets/{id}` is replace-not-merge: the `assets` "
                "array you send REPLACES the gallery — any asset omitted is "
                "unlinked. To edit asset titles safely use the typed "
                "media_set_update_asset_titles tool (GET-then-PUT-full-array); "
                "only hand-roll a media_sets PUT when you have the COMPLETE "
                "asset list. Same foot-gun as product `variants`.\n"
                "\n"
                "⚠️ Ordered `assets` arrays (`/media_sets/{id}`, ecommerce "
                "`/products/{id}`) are applied only PARTIALLY by roughly half "
                "of single PUTs — 200 either way. Write, read the order back, "
                "and repeat if it disagrees, or use the typed tools "
                "(media_set_set_assets, product_set_images) which do that."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "method": {
                        "type": "string",
                        "enum": list(ALLOWED_METHODS),
                        "description": "HTTP method",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. "
                            "'/forms', '/articles/42', "
                            "'/redirect_rules/9'. Must NOT be an absolute "
                            "URL — base host comes from the site config."
                        ),
                    },
                    "body": {
                        "type": ["object", "array", "null"],
                        "description": (
                            "Optional JSON body for POST/PUT/PATCH. "
                            "Voog uses different envelope conventions per "
                            "endpoint — see docs/voog-mcp-endpoint-coverage.md."
                        ),
                    },
                    "params": {
                        "type": ["object", "null"],
                        "description": (
                            "Optional query parameters as a flat string-keyed "
                            "object, e.g. {'include': 'translations', "
                            "'q.page.hidden.$eq': 'true'}."
                        ),
                    },
                },
                "required": ["site", "method", "path"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="voog_ecommerce_api_call",
            description=(
                "Generic Ecommerce v1 API WRITE passthrough. Forward an HTTP "
                "request to https://<host>/admin/api/ecommerce/v1<path>. "
                "Same shape as voog_admin_api_call, different base URL. "
                "Supports ?include=... and ?language_code=... per Voog "
                "ecommerce conventions. Use for orders, carts, discounts, "
                "shipping_methods, gateways, cart_fields, cart_rules, "
                "delivery_provider_configs, templates, bulk product "
                "actions, products imports, etc.\n"
                "\n"
                "For READS use voog_ecommerce_api_read — this tool no longer "
                "accepts method='GET' (removed in v1.5).\n"
                "\n"
                "PUT gotchas (Voog ecommerce v1 quirks — typed tools "
                "handle these for you, passthrough does not):\n"
                "  1. On `PUT /products/{id}`, asset references must use "
                'the `{"assets": [{"id": N}, ...]}` shape. Sending the '
                "POST-shape `asset_ids: [N, ...]` on PUT silently drops "
                "all but the hero image. Prefer `product_set_images` for "
                "image attachment; it handles the shape internally. The "
                "array ORDER is also applied only partially by about half "
                "of single PUTs (200 either way) — read `asset_ids` back "
                "and repeat the PUT until it matches.\n"
                "  2. On `PUT /products/{id}`, the `variants` array is "
                "destructive: Voog deletes every variant not present in "
                "the array — even variants with a stable `id`. Always "
                "include `variant_attributes` alongside `variants`, or "
                "send the full existing variant list. Prefer "
                "`product_update`; it requires explicit `force=true` to "
                "bypass this guard.\n"
                "  3. On `PUT` to endpoints that accept a `data` hash "
                "(e.g. `/pages/{id}`, `/articles/{id}`, `/site`), the "
                "`data` field REPLACES the entire hash — unspecified "
                "keys are dropped. Voog supports `PATCH` (merge "
                "semantics) on these routes; use method='PATCH' here, or "
                "prefer the per-key tools `page_set_data` / "
                "`article_set_data` / `site_set_data` which route "
                "through PATCH automatically (typed wrappers handle this "
                "as of v1.4)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "method": {
                        "type": "string",
                        "enum": list(ALLOWED_METHODS),
                        "description": "HTTP method",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. "
                            "'/orders', '/products/42', '/settings'."
                        ),
                    },
                    "body": {
                        "type": ["object", "array", "null"],
                        "description": (
                            "Optional JSON body for POST/PUT/PATCH. "
                            "Voog uses different envelope conventions per "
                            "endpoint — see docs/voog-mcp-endpoint-coverage.md."
                        ),
                    },
                    "params": {
                        "type": ["object", "null"],
                        "description": (
                            "Optional query parameters as a flat string-keyed "
                            "object, e.g. {'include': 'translations', "
                            "'q.page.hidden.$eq': 'true'}."
                        ),
                    },
                },
                "required": ["site", "method", "path"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset(
    {
        "voog_admin_api_read",
        "voog_ecommerce_api_read",
        "voog_admin_api_call",
        "voog_ecommerce_api_call",
    }
)


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")

    # S9: tag every HTTP request inside this dispatch with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring.
    with client.with_tool(name):
        if name == "voog_admin_api_read":
            return _passthrough_read(arguments, client, base=client.base_url, label="admin")
        if name == "voog_ecommerce_api_read":
            return _passthrough_read(
                arguments, client, base=client.ecommerce_url, label="ecommerce"
            )
        if name == "voog_admin_api_call":
            return _passthrough_call(arguments, client, base=client.base_url, label="admin")
        if name == "voog_ecommerce_api_call":
            return _passthrough_call(
                arguments, client, base=client.ecommerce_url, label="ecommerce"
            )

    return error_response(f"Unknown tool: {name}")


def _passthrough_read(
    arguments: dict, client: VoogClient, *, base: str, label: str
) -> list[TextContent] | CallToolResult:
    """GET-only passthrough surface for the new read tools."""
    path = arguments.get("path") or ""
    params = arguments.get("params")

    err = _validate_path(path)
    if err:
        return error_response(f"voog_{label}_api_read: {err}")

    if params is not None and not isinstance(params, dict):
        return error_response(f"voog_{label}_api_read: params must be an object or null")

    if params and "?" in path:
        return error_response(
            f"voog_{label}_api_read: path must not contain '?' when params is also set "
            f"(got path={path!r}, params={params!r}); pass query parameters via params= "
            f"OR embed them in path, not both"
        )

    try:
        data = client.get(path, base=base, params=params)
    except Exception as e:
        return error_response(f"voog_{label}_api_read GET {path} failed: {e}")

    return success_response(
        data,
        summary=f"🔌 GET {path} ({label} api, read) → ok",
    )


def _passthrough_call(
    arguments: dict, client: VoogClient, *, base: str, label: str
) -> list[TextContent] | CallToolResult:
    """Write-method passthrough (POST/PUT/PATCH/DELETE)."""
    method = (arguments.get("method") or "").upper()
    path = arguments.get("path") or ""
    body = arguments.get("body")
    params = arguments.get("params")

    if method not in ALLOWED_METHODS:
        moved_to = _REMOVED_METHOD_TARGET.get(method, {}).get(label)
        if moved_to:
            # A host that does not enforce the schema enum would otherwise
            # send GET straight through to a write-annotated tool. Name the
            # replacement rather than just listing what survives.
            return error_response(
                f"voog_{label}_api_call: method='GET' was removed in v1.5 — "
                f"use {moved_to} instead (same site/path/params arguments, "
                "read-only annotations so MCP hosts can skip the "
                "destructive-action prompt)."
            )
        return error_response(
            f"voog_{label}_api_call: method must be one of {ALLOWED_METHODS} (got {method!r})"
        )

    err = _validate_path(path)
    if err:
        return error_response(f"voog_{label}_api_call: {err}")

    if params is not None and not isinstance(params, dict):
        return error_response(f"voog_{label}_api_call: params must be an object or null")

    if params and "?" in path:
        return error_response(
            f"voog_{label}_api_call: path must not contain '?' when params is also set "
            f"(got path={path!r}, params={params!r}); pass query parameters via params= "
            f"OR embed them in path, not both"
        )

    try:
        # PR #124 review: forward `params` on every method. POST/PUT/PATCH
        # branches used to drop it (pre-existing pre-1.4); Phase 1a added
        # `params=` to every VoogClient method explicitly for consistency,
        # and the S8 filter-hatch makes "pass a Voog filter via query
        # string" a first-class pattern, so passthrough must forward it
        # too. Without this, voog_admin_api_call(method="POST", path=...,
        # body=..., params={"include": ...}) silently loses the include.
        if method == "DELETE":
            data = client.delete(path, base=base, params=params)
        elif method == "POST":
            data = client.post(path, body, base=base, params=params)
        elif method == "PUT":
            data = client.put(path, body, base=base, params=params)
        elif method == "PATCH":
            data = client.patch(path, body, base=base, params=params)
    except Exception as e:
        return error_response(f"voog_{label}_api_call {method} {path} failed: {e}")

    return success_response(data, summary=f"🔌 {method} {path} ({label} api) → ok")


def _validate_path(path: str) -> str | None:
    if not path:
        return "path must be non-empty"
    if "://" in path or path.startswith("//"):
        return f"path must not be an absolute URL (got {path!r})"
    if not path.startswith("/"):
        return f"path must start with '/' (got {path!r})"
    # Decode-until-stable so a proxy-normalised double-encoded ``..`` (e.g.
    # ``%252e%252e`` → ``%2e%2e`` → ``..``) can't slip past the literal check.
    decoded = _decode_until_stable(path)
    if ".." in decoded.split("/"):
        return f"path must not contain '..' segments (got {path!r})"
    return None
