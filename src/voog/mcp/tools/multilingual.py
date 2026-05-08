"""MCP tools for Voog multilingual primitives — languages and nodes.

Five tools:

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
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "language_create":
        return _language_create(arguments, client)

    if name == "language_delete":
        return _language_delete(arguments, client)

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
