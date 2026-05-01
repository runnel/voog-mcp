"""Generic Admin API + Ecommerce v1 API passthrough tools.

Two tools: ``voog_admin_api_call`` and ``voog_ecommerce_api_call``. Both take
``method`` + ``path`` + optional ``body`` and ``params``, then proxy through
the configured :class:`voog.client.VoogClient`. They cover endpoints the
typed tools haven't gotten to yet — orders, carts, discounts, shipping,
gateways, forms, tickets, elements, tags, media_sets, webhooks, bulk
operations, products imports, search, etc.

Why two tools instead of one with a ``base`` parameter: tool name carries
intent. ``voog_admin_api_call`` advertises "Admin API"; the ecommerce tool
advertises Ecommerce v1 (``?include=``, ``?language_code=``, anonymous-
allowed reads, etc.).

Annotations: both tools are ``destructiveHint=True`` because *any* method
is possible. Claude will surface a confirmation prompt before invoking
them. Callers pick a method explicitly; we don't try to guess intent.

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

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="voog_admin_api_call",
            description=(
                "Generic Admin API passthrough. Forward an HTTP request to "
                "https://<host>/admin/api<path> using the configured site's "
                "API token. method ∈ {GET, POST, PUT, PATCH, DELETE}; body "
                "is JSON-serialised on POST/PUT/PATCH. Use this when no typed "
                "tool covers the endpoint (orders, forms, tickets, elements, "
                "tags, media_sets, webhooks, etc.). Conservative annotations "
                "(destructiveHint=true) — Claude will confirm before calling."
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
                "actions, products imports, etc."
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


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "voog_admin_api_call":
        return _passthrough(arguments, client, base=client.base_url, label="admin")
    if name == "voog_ecommerce_api_call":
        return _passthrough(arguments, client, base=client.ecommerce_url, label="ecommerce")

    return error_response(f"Unknown tool: {name}")


def _passthrough(
    arguments: dict, client: VoogClient, *, base: str, label: str
) -> list[TextContent] | CallToolResult:
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

    # client._request appends ``?<urlencode(params)>`` blindly; if path also
    # contains a literal '?', the resulting URL is /x?a=1?b=2 (two query
    # markers, malformed). Reject at the tool boundary with a clear hint
    # — caller should use either path-with-query OR params=, not both.
    if params and "?" in path:
        return error_response(
            f"voog_{label}_api_call: path must not contain '?' when params is also set "
            f"(got path={path!r}, params={params!r}); pass query parameters via params= "
            f"OR embed them in path, not both"
        )

    try:
        if method == "GET":
            data = client.get(path, base=base, params=params)
        elif method == "DELETE":
            data = client.delete(path, base=base, params=params)
        elif method == "POST":
            data = client.post(path, body, base=base)
        elif method == "PUT":
            data = client.put(path, body, base=base)
        elif method == "PATCH":
            data = client.patch(path, body, base=base)
    except Exception as e:
        return error_response(f"voog_{label}_api_call {method} {path} failed: {e}")

    return success_response(
        data,
        summary=f"🔌 {method} {path} ({label} api) → ok",
    )


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
