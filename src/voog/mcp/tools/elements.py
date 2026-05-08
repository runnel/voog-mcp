"""MCP tools for Voog elements (structured catalog content).

Six tools (audit I6):

  - elements_list             — GET /elements (filterable)
  - element_get               — GET /elements/{id}
  - element_definitions_list  — GET /element_definitions (read-only)
  - element_create            — POST /elements
  - element_update            — PUT /elements/{id} (partial)
  - element_delete            — DELETE /elements/{id} (force gate)

Bodies are FLAT (no envelope). Element_create requires either
element_definition_id OR element_definition_title (id takes precedence
per Voog docs). element_definition mutation endpoints exist server-side
but are deferred — they're schema-level power-user ops; passthrough
handles when needed.

Element-move (PUT /elements/{id}/move) is also deferred — niche, and
the audit's "minimal set" listing doesn't include it.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_element_definitions, simplify_elements

# elements_list filter args forwarded as Voog query-string params.
_ELEMENTS_LIST_FILTERS = (
    "page_id",
    "language_id",
    "language_code",
    "element_definition_id",
    "element_definition_title",
    "page_path",
    "page_path_prefix",
    "include_values",
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="elements_list",
            description=(
                "List elements (id, title, path, page_id, "
                "element_definition_id, position). Optional filters: "
                "page_id, language_id, language_code, "
                "element_definition_id, element_definition_title, "
                "page_path, page_path_prefix. Pass include_values=true "
                "to include the values hash in the projection (off by "
                "default — values clutter list views; use element_get "
                "for full shape). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {
                        "type": "integer",
                        "description": "Filter to elements under this page id",
                    },
                    "language_id": {
                        "type": "integer",
                        "description": "Filter by language id (from languages_list)",
                    },
                    "language_code": {
                        "type": "string",
                        "minLength": 2,
                        "description": "Filter by ISO 639-1 language code",
                    },
                    "element_definition_id": {
                        "type": "integer",
                        "description": "Filter by definition id",
                    },
                    "element_definition_title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Filter by definition title (string match)",
                    },
                    "page_path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Filter to elements under this exact page path",
                    },
                    "page_path_prefix": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Filter to elements under any page path starting with this prefix",
                    },
                    "include_values": {
                        "type": "boolean",
                        "description": "Include the values hash in raw API response (Voog server-side)",
                    },
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
            name="element_get",
            description=(
                "Get a single element by id, with full values hash. Use elements_list to find ids."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "element_id": {
                        "type": "integer",
                        "description": "Voog element id (from elements_list)",
                    },
                },
                "required": ["site", "element_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="element_definitions_list",
            description=(
                "List element definitions (id, title, property_keys — "
                "the field keys each definition expects). Use the "
                "returned id for element_create.element_definition_id. "
                "Read-only. Mutating definitions (POST/PUT/DELETE) is "
                "deferred — handle via voog_admin_api_call when needed."
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
    ]


def _elements_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    params: dict = {}
    for key in _ELEMENTS_LIST_FILTERS:
        if arguments.get(key) is not None:
            params[key] = arguments[key]
    try:
        elements = client.get_all("/elements", params=params or None)
        simplified = simplify_elements(elements)
        return success_response(
            simplified,
            summary=f"🧩 {len(simplified)} elements",
        )
    except Exception as e:
        return error_response(f"elements_list failed: {e}")


def _element_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    element_id = arguments.get("element_id")
    try:
        element = client.get(f"/elements/{element_id}")
        return success_response(element)
    except Exception as e:
        return error_response(f"element_get id={element_id} failed: {e}")


def _element_definitions_list(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    try:
        definitions = client.get_all("/element_definitions")
        simplified = simplify_element_definitions(definitions)
        return success_response(
            simplified,
            summary=f"🧱 {len(simplified)} element definitions",
        )
    except Exception as e:
        return error_response(f"element_definitions_list failed: {e}")


_DISPATCH = {
    "elements_list": _elements_list,
    "element_get": _element_get,
    "element_definitions_list": _element_definitions_list,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
