"""MCP tools for Voog pages (read-only)."""
from mcp.types import Tool, TextContent

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response


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
        Tool(
            name="pages_pull",
            description="Return simplified pages structure as JSON (id, path, title, hidden, layout, content_type, language, public_url — no content bodies).",
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


async def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent]:
    arguments = arguments or {}
    if name == "pages_list":
        try:
            pages = client.get_all("/pages")
            simplified = _simplify_pages(pages)
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

    if name == "pages_pull":
        try:
            pages = client.get_all("/pages")
            simplified = _simplify_pages(pages)
            return success_response(simplified, summary=f"✓ pages-pull: {len(simplified)} entries")
        except Exception as e:
            return error_response(f"pages_pull ebaõnnestus: {e}")

    return error_response(f"Unknown tool: {name}")


def _simplify_pages(pages: list) -> list:
    """Project pages to simplified structure (matching voog.py pages_pull)."""
    simplified = []
    for p in pages:
        lang = p.get("language") or {}
        layout = p.get("layout") or {}
        simplified.append({
            "id": p.get("id"),
            "path": p.get("path"),
            "title": p.get("title"),
            "hidden": p.get("hidden"),
            "layout_id": p.get("layout_id") or layout.get("id"),
            "layout_name": p.get("layout_name") or p.get("layout_title") or layout.get("title"),
            "content_type": p.get("content_type"),
            "parent_id": p.get("parent_id"),
            "language_code": lang.get("code"),
            "public_url": p.get("public_url"),
        })
    return simplified
