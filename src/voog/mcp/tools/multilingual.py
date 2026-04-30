"""MCP tools for Voog multilingual primitives — languages and nodes.

Three read-only tools:

  - ``languages_list``  — GET /languages, simplified projection. Use
                           the returned ids for page_create.language_id
                           / article_update etc.
  - ``nodes_list``      — GET /nodes, simplified projection.
  - ``node_get``        — GET /nodes/{id}, full object including the
                           parallel-translation pages array. Use when
                           preparing page_create(node_id=...) for a
                           parallel translation.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_languages, simplify_nodes


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="languages_list",
            description=(
                "List all languages on the Voog site (id, code, title, "
                "default_language, published, position). Use the returned "
                "ids for page_create.language_id / article fields. "
                "Read-only."
            ),
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
            name="nodes_list",
            description=(
                "List all page nodes (id, title, parent_id, position). "
                "Each node represents a language-agnostic page identity; "
                "its parallel translations are pages sharing the same "
                "node.id. Read-only."
            ),
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
            name="node_get",
            description=(
                "Get a single node by id, with its full pages array — one "
                "entry per language. Use this when preparing a parallel "
                "translation: read the node id from one page, then pass "
                "node_id to page_create with the second-language details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "node_id": {"type": "integer"},
                },
                "required": ["site", "node_id"],
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

    if name == "languages_list":
        try:
            langs = client.get_all("/languages")
            simplified = simplify_languages(langs)
            return success_response(simplified, summary=f"🌐 {len(simplified)} languages")
        except Exception as e:
            return error_response(f"languages_list failed: {e}")

    if name == "nodes_list":
        try:
            nodes = client.get_all("/nodes")
            simplified = simplify_nodes(nodes)
            return success_response(simplified, summary=f"🌳 {len(simplified)} nodes")
        except Exception as e:
            return error_response(f"nodes_list failed: {e}")

    if name == "node_get":
        node_id = arguments.get("node_id")
        try:
            node = client.get(f"/nodes/{node_id}")
            return success_response(node)
        except Exception as e:
            return error_response(f"node_get id={node_id} failed: {e}")

    return error_response(f"Unknown tool: {name}")
