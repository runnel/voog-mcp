"""MCP tools for Voog pages (read-only)."""
from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import success_response, error_response
from voog.projections import simplify_pages


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="pages_list",
            description="List all pages on the Voog site (id, path, title, hidden, layout name). Read-only.",
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_get",
            description="Get full details of a single page by id (title, path, hidden, layout, language, parent, timestamps, public_url).",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "integer", "description": "Voog page id"}
                },
                "required": ["page_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}
    if name == "pages_list":
        try:
            pages = client.get_all("/pages")
            simplified = simplify_pages(pages)
            return success_response(simplified, summary=f"📄 {len(simplified)} pages")
        except Exception as e:
            return error_response(f"pages_list ebaõnnestus: {e}")

    if name == "page_get":
        page_id = arguments.get("page_id")
        try:
            p = client.get(f"/pages/{page_id}")
            return success_response(p)
        except Exception as e:
            return error_response(f"page_get id={page_id} ebaõnnestus: {e}")

    return error_response(f"Unknown tool: {name}")
