"""MCP tools for Voog multilingual primitives — languages and nodes.

Eight tools:

  - ``language_create`` — POST /languages, add a new site language.
  - ``language_delete`` — DELETE /languages/{id}, remove a language (force gate).
  - ``languages_list``  — GET /languages, simplified projection. Use
                           the returned ids for page_create.language_id
                           / article_update etc.
  - ``nodes_list``      — GET /nodes, simplified projection.
  - ``node_get``        — GET /nodes/{id}, full object including the
                           parallel-translation pages array. Use when
                           preparing page_create(node_id=...) for a
                           parallel translation.
  - ``node_update``     — PUT /nodes/{id}, update a node's title.
  - ``node_move``       — PUT /nodes/{id}/move, move/reorder a node
                           within the tree via query-string params.
  - ``node_relocate``   — PUT /nodes/{id}/relocate, place a node at a
                           precise position relative to a sibling or
                           under a new parent (flat body, exactly one
                           of before/after/parent_node_id).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_languages, simplify_nodes


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="language_create",
            description=(
                "Add a new language to the Voog site (POST /languages). "
                "Required: code (ISO 639-1 two-letter), title. Optional: "
                "region (ISO 3166-1 alpha-2), site_title, site_header, "
                "default_language, published, content_origin_id "
                "(duplicate content from another language). Body is FLAT — "
                "no envelope wrapper."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "code": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 2,
                        "description": (
                            "ISO 639-1 two-letter code (e.g. 'et', 'en'). "
                            "Voog stores region separately — pass 'region' "
                            "for variants like en-GB, NOT 'en-GB' here."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Language name shown in the language menu",
                    },
                    "region": {
                        "type": "string",
                        "description": "ISO 3166-1 alpha-2 region code (optional)",
                    },
                    "site_title": {
                        "type": "string",
                        "description": "Per-language HTML title (optional)",
                    },
                    "site_header": {
                        "type": "string",
                        "description": "Per-language content header (optional)",
                    },
                    "default_language": {
                        "type": "boolean",
                        "description": "Make this the site's default language",
                    },
                    "published": {
                        "type": "boolean",
                        "description": "Whether the language is publicly visible (default true)",
                    },
                    "content_origin_id": {
                        "type": "integer",
                        "description": "Duplicate content from this existing language id",
                    },
                },
                "required": ["site", "code", "title"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="language_delete",
            description=(
                "Remove a language from the site (DELETE /languages/{id}). "
                "IRREVERSIBLE — Voog deletes the language and unbinds "
                "associated content. Requires force=true; without it the "
                "call is rejected. Run site_snapshot first if uncertain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "language_id": {
                        "type": "integer",
                        "description": "Voog language id (from languages_list)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Must be true to actually perform the delete. "
                            "Defaults to false (defensive opt-in)."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "language_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
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
        Tool(
            name="node_update",
            description=(
                "Update a node's title (PUT /nodes/{id}). Per Voog docs, "
                "only `title` is documented as updatable. Body is FLAT — "
                "no envelope wrapper. For tree restructuring use "
                "node_move (parent + position) or node_relocate "
                "(positional placement)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "node_id": {
                        "type": "integer",
                        "description": "Voog node id (from nodes_list)",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "New node title",
                    },
                },
                "required": ["site", "node_id", "title"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="node_move",
            description=(
                "Move/reorder a node within the page tree (PUT "
                "/nodes/{id}/move). Inputs travel as query-string "
                "params per Voog docs. Required: parent_id (current "
                "or new parent — pass current to just reorder). "
                "Optional: position (1-indexed, Voog default 1)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "node_id": {
                        "type": "integer",
                        "description": "Voog node id to move",
                    },
                    "parent_id": {
                        "type": "integer",
                        "description": "Current or new parent node id",
                    },
                    "position": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "New position under parent (1-indexed). Omit to let Voog default to 1.",
                    },
                },
                "required": ["site", "node_id", "parent_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="node_relocate",
            description=(
                "Relocate a node to a precise position relative to a "
                "sibling, or to the first slot under a new parent (PUT "
                "/nodes/{id}/relocate). Body is FLAT. Supply EXACTLY ONE "
                "of: before (place this node before the given sibling "
                "id), after (place after sibling id), or parent_node_id "
                "(move to first position under new parent). Mutually "
                "exclusive — handler rejects multiple."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "node_id": {
                        "type": "integer",
                        "description": "Voog node id to relocate",
                    },
                    "before": {
                        "type": "integer",
                        "description": "Sibling node id; place this node before it",
                    },
                    "after": {
                        "type": "integer",
                        "description": "Sibling node id; place this node after it",
                    },
                    "parent_node_id": {
                        "type": "integer",
                        "description": "New parent node id (moves to first position)",
                    },
                },
                "required": ["site", "node_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def _languages_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        langs = client.get_all("/languages")
        simplified = simplify_languages(langs)
        return success_response(simplified, summary=f"🌐 {len(simplified)} languages")
    except Exception as e:
        return error_response(f"languages_list failed: {e}")


def _nodes_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    try:
        nodes = client.get_all("/nodes")
        simplified = simplify_nodes(nodes)
        return success_response(simplified, summary=f"🌳 {len(simplified)} nodes")
    except Exception as e:
        return error_response(f"nodes_list failed: {e}")


def _node_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    node_id = arguments.get("node_id")
    try:
        node = client.get(f"/nodes/{node_id}")
        return success_response(node)
    except Exception as e:
        return error_response(f"node_get id={node_id} failed: {e}")


_LANGUAGE_CREATE_FIELDS = (
    "code",
    "title",
    "region",
    "site_title",
    "site_header",
    "default_language",
    "published",
    "content_origin_id",
)


def _language_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    code = arguments.get("code") or ""
    title = arguments.get("title") or ""
    if not code.strip():
        return error_response("language_create: code is required")
    if not title.strip():
        return error_response("language_create: title is required")

    # Flat body — no {"language": {...}} wrapper per Voog docs.
    body: dict = {}
    for key in _LANGUAGE_CREATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]

    try:
        result = client.post("/languages", body)
        new_id = result.get("id") if isinstance(result, dict) else None
        return success_response(
            result,
            summary=(
                f"language created (id={new_id}, code={code})"
                if new_id
                else f"language created (code={code})"
            ),
        )
    except Exception as e:
        return error_response(f"language_create code={code!r} failed: {e}")


def _language_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    language_id = arguments.get("language_id")
    if not arguments.get("force"):
        return error_response(
            f"language_delete: refusing to delete language {language_id} without force=true. "
            "IRREVERSIBLE — Voog deletes the language and unbinds associated content. "
            "Run site_snapshot first if uncertain, then set force=true."
        )
    try:
        client.delete(f"/languages/{language_id}")
        return success_response(
            {"deleted": {"language_id": language_id}},
            summary=f"🗑️  language {language_id} deleted",
        )
    except Exception as e:
        return error_response(f"language_delete id={language_id} failed: {e}")


def _node_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    node_id = arguments.get("node_id")
    title = arguments.get("title") or ""
    if not title.strip():
        return error_response("node_update: title must be non-empty")
    try:
        result = client.put(f"/nodes/{node_id}", {"title": title})
        return success_response(
            result,
            summary=f"🌳 node {node_id} updated: title={title!r}",
        )
    except Exception as e:
        return error_response(f"node_update id={node_id} failed: {e}")


def _node_move(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    node_id = arguments.get("node_id")
    parent_id = arguments.get("parent_id")
    # `bool` is a subclass of int — reject it explicitly so True/False
    # don't slip through as 1/0 and confuse Voog. PR #113 review.
    if not isinstance(parent_id, int) or isinstance(parent_id, bool):
        return error_response("node_move: parent_id is required (integer)")
    params: dict = {"parent_id": parent_id}
    position = arguments.get("position")
    if position is not None:
        if not isinstance(position, int) or isinstance(position, bool):
            return error_response("node_move: position must be an integer")
        params["position"] = position
    try:
        result = client.put(f"/nodes/{node_id}/move", params=params)
        return success_response(
            result,
            summary=(
                f"🌳 node {node_id} moved → parent={parent_id}, "
                f"position={params.get('position', 'default')}"
            ),
        )
    except Exception as e:
        return error_response(f"node_move id={node_id} failed: {e}")


_NODE_RELOCATE_FIELDS = ("before", "after", "parent_node_id")


def _node_relocate(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    node_id = arguments.get("node_id")
    supplied = [k for k in _NODE_RELOCATE_FIELDS if arguments.get(k) is not None]
    if not supplied:
        return error_response("node_relocate: supply exactly one of before, after, parent_node_id")
    if len(supplied) > 1:
        return error_response(
            f"node_relocate: supplied {supplied}; the three positional "
            "fields are mutually exclusive — pick one"
        )
    key = supplied[0]
    body = {key: arguments[key]}
    try:
        result = client.put(f"/nodes/{node_id}/relocate", body)
        return success_response(
            result,
            summary=f"🌳 node {node_id} relocated: {key}={arguments[key]}",
        )
    except Exception as e:
        return error_response(f"node_relocate id={node_id} failed: {e}")


_DISPATCH = {
    "language_create": _language_create,
    "language_delete": _language_delete,
    "languages_list": _languages_list,
    "node_get": _node_get,
    "node_move": _node_move,
    "node_relocate": _node_relocate,
    "node_update": _node_update,
    "nodes_list": _nodes_list,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
