"""Generic Admin API + Ecommerce v1 API passthrough tools.

Four tools:

  - ``voog_admin_api_read``       — readOnlyHint=true GET-only passthrough
                                     for Admin API endpoints not yet covered
                                     by typed tools.
  - ``voog_ecommerce_api_read``   — readOnlyHint=true GET-only passthrough
                                     for Ecommerce v1 endpoints.
  - ``voog_admin_api_call``       — full-method passthrough (POST/PUT/PATCH/
                                     DELETE/GET); GET is DEPRECATED in v1.4,
                                     removed in v1.5. Use voog_admin_api_read.
  - ``voog_ecommerce_api_call``   — full-method passthrough for Ecommerce v1;
                                     GET DEPRECATED, see read tool.

The split closes S3 (v4 audit): conservative annotations
(``destructiveHint=True``) on the write-capable tools cause MCP hosts to
surface confirmation prompts on every call — for read-only traffic that's
alarm fatigue. The new read tools advertise ``readOnlyHint=true`` and
hosts can skip the prompt.

GET deprecation channels (v1.4):
  1. ``warnings.warn(..., DeprecationWarning)`` — visible to CLI / tests /
     stderr (Python process channel).
  2. MCP TextContent summary prefix ``DEPRECATED: ...`` — visible to the
     MCP host and the LLM (MCP transport channel).

Both channels are required because ``warnings.warn`` alone is invisible to
MCP hosts. v1.5 hard-removes GET from the *call* tools.

Path validation rejects three obvious foot-guns:
  - Empty path or path without a leading ``/`` (would build an invalid URL).
  - Absolute URL (would let the caller bypass the configured host — a
    secret-exfiltration vector if the response is logged).
  - ``..`` segments (no legitimate Voog endpoint contains them; refusing
    them is cheap defence-in-depth).
"""

import warnings

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import _decode_until_stable, strip_site

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Deprecation banners shipped on the GET branch of the legacy *_call tools.
# v1.5 removes GET entirely from these tools — callers should migrate to
# the corresponding *_read tool.
_ADMIN_GET_DEPRECATION_MSG = (
    "voog_admin_api_call(method='GET', ...) is deprecated; "
    "use voog_admin_api_read. GET support removed in v1.5."
)
_ECOM_GET_DEPRECATION_MSG = (
    "voog_ecommerce_api_call(method='GET', ...) is deprecated; "
    "use voog_ecommerce_api_read. GET support removed in v1.5."
)
_ADMIN_GET_DEPRECATION_PREFIX = (
    "DEPRECATED: this tool will lose GET support in v1.5; use voog_admin_api_read instead.\n\n"
)
_ECOM_GET_DEPRECATION_PREFIX = (
    "DEPRECATED: this tool will lose GET support in v1.5; use voog_ecommerce_api_read instead.\n\n"
)


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
                "Generic Admin API passthrough. Forward an HTTP request to "
                "https://<host>/admin/api<path> using the configured site's "
                "API token. method ∈ {GET, POST, PUT, PATCH, DELETE}; body "
                "is JSON-serialised on POST/PUT/PATCH. Use this when no typed "
                "tool covers the endpoint (orders, forms, tickets, elements, "
                "tags, media_sets, webhooks, etc.). Conservative annotations "
                "(destructiveHint=true) — Claude will confirm before calling.\n"
                "\n"
                "method='GET' is DEPRECATED in v1.4 — use voog_admin_api_read "
                "instead; GET support is removed in v1.5."
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
                "Generic Ecommerce v1 API passthrough. Forward an HTTP "
                "request to https://<host>/admin/api/ecommerce/v1<path>. "
                "Same shape as voog_admin_api_call, different base URL. "
                "Supports ?include=... and ?language_code=... per Voog "
                "ecommerce conventions. Use for orders, carts, discounts, "
                "shipping_methods, gateways, cart_fields, cart_rules, "
                "delivery_provider_configs, templates, bulk product "
                "actions, products imports, etc.\n"
                "\n"
                "method='GET' is DEPRECATED in v1.4 — use "
                "voog_ecommerce_api_read instead; GET support is removed "
                "in v1.5."
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
            return _passthrough_call(
                arguments,
                client,
                base=client.base_url,
                label="admin",
                deprecation_msg=_ADMIN_GET_DEPRECATION_MSG,
                deprecation_prefix=_ADMIN_GET_DEPRECATION_PREFIX,
            )
        if name == "voog_ecommerce_api_call":
            return _passthrough_call(
                arguments,
                client,
                base=client.ecommerce_url,
                label="ecommerce",
                deprecation_msg=_ECOM_GET_DEPRECATION_MSG,
                deprecation_prefix=_ECOM_GET_DEPRECATION_PREFIX,
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
    arguments: dict,
    client: VoogClient,
    *,
    base: str,
    label: str,
    deprecation_msg: str,
    deprecation_prefix: str,
) -> list[TextContent] | CallToolResult:
    """Full-method passthrough; GET branch emits two-channel deprecation."""
    method = (arguments.get("method") or "").upper()
    path = arguments.get("path") or ""
    body = arguments.get("body")
    params = arguments.get("params")

    if method not in ALLOWED_METHODS:
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

    is_get = method == "GET"
    if is_get:
        # Python-side channel — visible to CLI / tests / stderr.
        warnings.warn(deprecation_msg, DeprecationWarning, stacklevel=2)

    try:
        # PR #124 review: forward `params` on every method. POST/PUT/PATCH
        # branches used to drop it (pre-existing pre-1.4); Phase 1a added
        # `params=` to every VoogClient method explicitly for consistency,
        # and the S8 filter-hatch makes "pass a Voog filter via query
        # string" a first-class pattern, so passthrough must forward it
        # too. Without this, voog_admin_api_call(method="POST", path=...,
        # body=..., params={"include": ...}) silently loses the include.
        if method == "GET":
            data = client.get(path, base=base, params=params)
        elif method == "DELETE":
            data = client.delete(path, base=base, params=params)
        elif method == "POST":
            data = client.post(path, body, base=base, params=params)
        elif method == "PUT":
            data = client.put(path, body, base=base, params=params)
        elif method == "PATCH":
            data = client.patch(path, body, base=base, params=params)
    except Exception as e:
        return error_response(f"voog_{label}_api_call {method} {path} failed: {e}")

    summary = f"🔌 {method} {path} ({label} api) → ok"
    if is_get:
        # MCP-transport channel — visible to MCP host + LLM.
        summary = deprecation_prefix + summary
    return success_response(data, summary=summary)


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
