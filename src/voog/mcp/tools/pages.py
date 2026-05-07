"""MCP tools for Voog pages (read-only)."""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_pages

# q.* filters (object.attribute on the resource).
_PAGES_Q_FILTERS = {
    "language_code": "q.page.language_code",
    "content_type": "q.page.content_type",
    "node_id": "q.page.node_id",
}

# Plain endpoint params — supported by GET /pages but NOT expressible as
# q.* filters (multi-field search, prefix matching, parent traversal).
# See https://www.voog.com/developers/api/resources/pages.
_PAGES_PLAIN_PARAMS = ("path_prefix", "search", "parent_id", "language_id")


def _build_pages_list_params(arguments: dict) -> dict | None:
    """Translate tool args to Voog query params. Returns None when no
    filters are set, so the caller falls through to the unparameterised
    `client.get_all("/pages")` shape."""
    params: dict = {}
    for arg_key, voog_key in _PAGES_Q_FILTERS.items():
        if arg_key in arguments:
            params[voog_key] = arguments[arg_key]
    for arg_key in _PAGES_PLAIN_PARAMS:
        if arg_key in arguments:
            params[arg_key] = arguments[arg_key]
    if "sort" in arguments:
        params["s"] = arguments["sort"]
    return params or None


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="pages_list",
            description="List all pages on the Voog site (id, path, title, hidden, layout name). Read-only.",
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
            name="page_get",
            description="Get full details of a single page by id (title, path, hidden, layout, language, parent, timestamps, public_url).",
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "page_id": {"type": "integer", "description": "Voog page id"},
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    if name == "pages_list":
        params = _build_pages_list_params(arguments)
        try:
            if params:
                pages = client.get_all("/pages", params=params)
            else:
                pages = client.get_all("/pages")
            simplified = simplify_pages(pages)
            return success_response(simplified, summary=f"📄 {len(simplified)} pages")
        except Exception as e:
            return error_response(f"pages_list failed: {e}")

    if name == "page_get":
        page_id = arguments.get("page_id")
        try:
            p = client.get(f"/pages/{page_id}")
            return success_response(p)
        except Exception as e:
            return error_response(f"page_get id={page_id} failed: {e}")

    return error_response(f"Unknown tool: {name}")
