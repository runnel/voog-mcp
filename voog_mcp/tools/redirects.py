"""MCP tools for Voog redirect rules."""
from mcp.types import CallToolResult, TextContent, Tool

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response


VALID_REDIRECT_TYPES = [301, 302, 307, 410]


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="redirects_list",
            description="List all redirect rules on the Voog site (id, source, destination, redirect_type, active). Read-only.",
            inputSchema={"type": "object", "properties": {}, "required": []},
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
                    "source": {"type": "string", "description": "Source path (e.g. /en/products/old)"},
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
                "required": ["source", "destination"],
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
    ]


def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}

    if name == "redirects_list":
        try:
            rules = client.get_all("/redirect_rules")
            return success_response(rules, summary=f"↪️  {len(rules)} redirect rules")
        except Exception as e:
            return error_response(f"redirects_list ebaõnnestus: {e}")

    if name == "redirect_add":
        source = arguments.get("source")
        destination = arguments.get("destination")
        rtype = arguments.get("redirect_type", 301)
        try:
            result = client.post(
                "/redirect_rules",
                {
                    "redirect_rule": {
                        "source": source,
                        "destination": destination,
                        "redirect_type": rtype,
                        "active": True,
                    }
                },
            )
            return success_response(
                result,
                summary=f"✅ {source} → {destination} ({rtype})",
            )
        except Exception as e:
            return error_response(f"redirect_add ebaõnnestus: {e}")

    return error_response(f"Unknown tool: {name}")
