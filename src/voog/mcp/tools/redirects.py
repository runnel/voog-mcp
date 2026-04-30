"""MCP tools for Voog redirect rules."""

from mcp.types import CallToolResult, TextContent, Tool

from voog._payloads import build_redirect_payload
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

VALID_REDIRECT_TYPES = [301, 302, 307, 410]


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="redirects_list",
            description="List all redirect rules on the Voog site (id, source, destination, redirect_type, active). Read-only.",
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
            name="redirect_add",
            description=(
                "Add a redirect rule. source/destination are paths (e.g. /old → /new). "
                "redirect_type defaults to 301; allowed: 301, 302, 307, 410. "
                "For 410 (Gone), destination is semantically meaningless — Voog still "
                "stores it but never redirects there; pass any value (e.g. source path)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "source": {
                        "type": "string",
                        "description": "Source path (e.g. /en/products/old)",
                    },
                    "destination": {
                        "type": "string",
                        "description": (
                            "Destination path (e.g. /en/products/new). Ignored when "
                            "redirect_type=410 (Gone) — 410 returns the status without redirecting."
                        ),
                    },
                    "redirect_type": {
                        "type": "integer",
                        "description": "HTTP status code: 301 (permanent), 302 (temporary), 307 (temporary, preserve method), 410 (gone — destination ignored). Default 301.",
                        "enum": VALID_REDIRECT_TYPES,
                        "default": 301,
                    },
                },
                "required": ["site", "source", "destination"],
            },
            # Explicit annotations — MCP spec defaults destructiveHint to true
            # when readOnlyHint is false. redirect_add is additive in storage
            # (creates a new rule, doesn't remove one), so destructiveHint=False.
            # idempotentHint=False: repeat calls with the same source/destination
            # either create a duplicate rule or trigger a Voog API conflict —
            # repeated calls have additional effect.
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="redirect_update",
            description=(
                "Update an existing redirect rule. At least one of source, "
                "destination, redirect_type, active must be supplied. "
                "redirect_type ∈ {301, 302, 307, 410}. Reversible by "
                "calling again with previous values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "redirect_id": {"type": "integer"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "redirect_type": {
                        "type": "integer",
                        "enum": VALID_REDIRECT_TYPES,
                    },
                    "active": {"type": "boolean"},
                },
                "required": ["site", "redirect_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="redirect_delete",
            description=(
                "Delete a redirect rule. Refuses without force=true. "
                "Reversible only by re-creating the rule via redirect_add."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "redirect_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "redirect_id"],
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

    if name == "redirects_list":
        try:
            rules = client.get_all("/redirect_rules")
            return success_response(rules, summary=f"↪️  {len(rules)} redirect rules")
        except Exception as e:
            return error_response(f"redirects_list failed: {e}")

    if name == "redirect_add":
        source = arguments.get("source")
        destination = arguments.get("destination")
        rtype = arguments.get("redirect_type", 301)
        try:
            result = client.post(
                "/redirect_rules",
                build_redirect_payload(source, destination, redirect_type=rtype),
            )
            return success_response(
                result,
                summary=f"✅ {source} → {destination} ({rtype})",
            )
        except Exception as e:
            return error_response(f"redirect_add failed: {e}")

    if name == "redirect_update":
        redirect_id = arguments.get("redirect_id")
        rtype = arguments.get("redirect_type")
        if rtype is not None and rtype not in VALID_REDIRECT_TYPES:
            return error_response(
                f"redirect_update: invalid redirect_type {rtype!r}; "
                f"allowed: {VALID_REDIRECT_TYPES}"
            )
        rule_body: dict = {}
        for key in ("source", "destination", "redirect_type", "active"):
            if key in arguments:
                rule_body[key] = arguments[key]
        if not rule_body:
            return error_response(
                "redirect_update: at least one of source/destination/"
                "redirect_type/active must be supplied"
            )
        try:
            result = client.put(
                f"/redirect_rules/{redirect_id}",
                {"redirect_rule": rule_body},
            )
            return success_response(
                result,
                summary=f"↪️  redirect {redirect_id} updated: {sorted(rule_body.keys())}",
            )
        except Exception as e:
            return error_response(f"redirect_update id={redirect_id} failed: {e}")

    if name == "redirect_delete":
        redirect_id = arguments.get("redirect_id")
        if not arguments.get("force"):
            return error_response(
                f"redirect_delete: refusing to delete rule {redirect_id} without force=true"
            )
        try:
            client.delete(f"/redirect_rules/{redirect_id}")
            return success_response(
                {"deleted": redirect_id},
                summary=f"🗑️  redirect {redirect_id} deleted",
            )
        except Exception as e:
            return error_response(f"redirect_delete id={redirect_id} failed: {e}")

    return error_response(f"Unknown tool: {name}")
