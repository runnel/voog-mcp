"""MCP tools for the Voog admin /site singleton.

Four tools:
  - ``site_get``         — GET /site
  - ``site_update``      — PUT /site (flat body, no envelope)
  - ``site_set_data``    — PUT /site/data/{key}  (PUT-only, non-destructive)
  - ``site_delete_data`` — DELETE /site/data/{key} (requires force=True)

Skill-memory rules captured:
  - site.code is immutable once set (and once site has paid plan).
    Refused client-side.
  - data.internal_* keys are server-protected. Refused client-side.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import _validate_data_key, strip_site

# Fields that must never be PUT back to /site:
#   - `code` is immutable per Voog (and project memory).
#   - `id`, `created_at`, `updated_at` are server-managed — round-tripping
#     a GET response into a PUT would either silently no-op or 422.
# (Not added: `plan`, `currently_paid_until`, `languages` — those need
# more thought; out of scope here.)
IMMUTABLE_SITE_FIELDS = frozenset(["code", "id", "created_at", "updated_at"])


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
                "Set site.data.<key> to a value (PUT /site/data/{key}). "
                "To delete a key use site_delete_data. "
                "'internal_*' keys are server-protected and refused "
                "client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array"],
                    },
                },
                "required": ["site", "key", "value"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="site_delete_data",
            description=(
                "Delete site.data.<key> (DELETE /site/data/{key}). "
                "IRREVERSIBLE — the key is removed from site.data permanently. "
                "Requires force=true; without it the call is rejected. "
                "'internal_*' keys are server-protected and refused client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "key": {"type": "string"},
                    "force": {
                        "type": "boolean",
                        "description": "Must be true to actually perform the delete. Defaults to false (defensive opt-in).",
                        "default": False,
                    },
                },
                "required": ["site", "key"],
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
            return error_response(f"site_update: fields {sorted(forbidden)} are immutable")
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
        err = _validate_data_key(key, tool_name="site_set_data")
        if err:
            return error_response(err)
        try:
            return success_response(
                client.put(f"/site/data/{key}", {"value": value}),
                summary=f"site.data.{key} set",
            )
        except Exception as e:
            return error_response(f"site_set_data key={key!r} failed: {e}")

    if name == "site_delete_data":
        key = arguments.get("key") or ""
        force = bool(arguments.get("force"))
        err = _validate_data_key(key, tool_name="site_delete_data")
        if err:
            return error_response(err)
        if not force:
            return error_response(
                f"site_delete_data: refusing to delete site.data.{key!r} without force=true. "
                "Set force=true after confirming the deletion is intentional."
            )
        try:
            client.delete(f"/site/data/{key}")
            return success_response(
                {"deleted": {"key": key}},
                summary=f"site.data.{key} deleted",
            )
        except Exception as e:
            return error_response(f"site_delete_data key={key!r} failed: {e}")

    return error_response(f"Unknown tool: {name}")
