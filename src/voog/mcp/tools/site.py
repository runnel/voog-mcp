"""MCP tools for the Voog admin /site singleton.

Three tools:
  - ``site_get``       — GET /site
  - ``site_update``    — PUT /site (flat body, no envelope)
  - ``site_set_data``  — PUT/DELETE /site/data/{key}

Skill-memory rules captured:
  - site.code is immutable once set (and once site has paid plan).
    Refused client-side.
  - data.internal_* keys are server-protected. Refused client-side.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

IMMUTABLE_SITE_FIELDS = frozenset(["code"])


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="site_get",
            description="Get the site singleton (title, code, data, languages, ...). Read-only.",
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
            name="site_update",
            description=(
                "Update site singleton. attributes: flat root-level fields. "
                "site.code is immutable once set — passing it raises an "
                "error. For per-key data, use site_set_data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "attributes": {"type": "object"},
                },
                "required": ["site", "attributes"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="site_set_data",
            description=(
                "Set or delete site.data.<key>. value=null deletes the "
                "key. 'internal_*' keys are server-protected and refused "
                "client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array", "null"],
                    },
                },
                "required": ["site", "key"],
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

    if name == "site_get":
        try:
            return success_response(client.get("/site"))
        except Exception as e:
            return error_response(f"site_get failed: {e}")

    if name == "site_update":
        attributes = arguments.get("attributes") or {}
        if not attributes:
            return error_response("site_update: attributes must be non-empty")
        forbidden = set(attributes) & IMMUTABLE_SITE_FIELDS
        if forbidden:
            return error_response(
                f"site_update: fields {sorted(forbidden)} are immutable"
            )
        try:
            return success_response(
                client.put("/site", attributes),
                summary=f"site updated: {sorted(attributes.keys())}",
            )
        except Exception as e:
            return error_response(f"site_update failed: {e}")

    if name == "site_set_data":
        key = arguments.get("key") or ""
        value = arguments.get("value")
        if not key.strip():
            return error_response("site_set_data: key must be non-empty")
        if key.startswith("internal_"):
            return error_response(
                f"site_set_data: 'internal_' keys are server-protected (got {key!r})"
            )
        try:
            if value is None:
                client.delete(f"/site/data/{key}")
                return success_response(
                    {"deleted": {"key": key}},
                    summary=f"site.data.{key} deleted",
                )
            return success_response(
                client.put(f"/site/data/{key}", {"value": value}),
                summary=f"site.data.{key} set",
            )
        except Exception as e:
            return error_response(f"site_set_data key={key!r} failed: {e}")

    return error_response(f"Unknown tool: {name}")
