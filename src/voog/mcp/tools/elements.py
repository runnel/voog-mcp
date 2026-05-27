"""MCP tools for Voog elements (structured catalog content).

Seven tools (audit I6 + v1.4 Phase 3 S13):

  - elements_list             — GET /elements (filterable)
  - element_get               — GET /elements/{id}
  - element_definitions_list  — GET /element_definitions (read-only)
  - element_create            — POST /elements
  - element_update            — PUT /elements/{id} (partial)
  - element_delete            — DELETE /elements/{id} (force gate)
  - element_move              — PUT /elements/{id}/move (query-string params)

Bodies for CRUD ops are FLAT (no envelope). `element_create` requires either
`element_definition_id` OR `element_definition_title` (id takes precedence
per Voog docs). element_definition mutation endpoints exist server-side
but are deferred — schema-level power-user ops; passthrough handles when
needed. N5 scope note: `element_move` operates on element INSTANCES,
not on element_definitions.

`element_move` is a special shape: per Voog docs it takes its inputs
as QUERY-STRING params (mirrors `node_move`), NOT a JSON body. Params:
`page_id` (new parent page id), `before` (element id to position
before), `after` (element id to position after). Docs:
https://www.voog.com/developers/api/resources/elements
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import (
    build_list_params,
    require_force,
    require_int,
    strip_site,
    validate_filters,
)
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
                        "description": (
                            "When true, Voog populates each element's "
                            "values hash AND the MCP projection includes "
                            "it in the returned list. Default false — "
                            "values clutter list views; use element_get "
                            "for full per-element shape."
                        ),
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "Escape hatch for Voog filter keys not exposed "
                            "as typed args. Keys MUST match "
                            "q.element.<attr>.(\\$eq|\\$cont|\\$gteq|\\$lteq|"
                            "\\$gt|\\$lt|\\$in|\\$nin|\\$starts|\\$ends|"
                            "\\$null|\\$has)."
                        ),
                        "additionalProperties": {"type": ["string", "integer", "boolean"]},
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
        Tool(
            name="element_create",
            description=(
                "Create an element (POST /elements). Body is FLAT. "
                "Required: element_definition_id OR "
                "element_definition_title (id takes precedence per Voog "
                "docs); page_id; title. Optional: path (auto-generated "
                "from title if omitted), values (custom-properties hash "
                "matching the element_definition's schema)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "element_definition_id": {
                        "type": "integer",
                        "description": "Definition id (from element_definitions_list)",
                    },
                    "element_definition_title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Alternative to element_definition_id (id wins if both supplied)",
                    },
                    "page_id": {
                        "type": "integer",
                        "description": "Parent page id (from pages_list)",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Element title",
                    },
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "URL slug (auto-generated from title if omitted)",
                    },
                    "values": {
                        "type": "object",
                        "description": "Custom-properties hash matching the element_definition's schema",
                    },
                },
                "required": ["site", "page_id", "title"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="element_update",
            description=(
                "Update an element (PUT /elements/{id}). Partial — supply "
                "ONLY the fields to change. Body is FLAT. Updatable: "
                "title, path, values. At least one besides element_id is "
                "required."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "element_id": {
                        "type": "integer",
                        "description": "Voog element id (from elements_list)",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": "New title",
                    },
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "New URL slug",
                    },
                    "values": {
                        "type": "object",
                        "description": "Replacement values hash",
                    },
                },
                "required": ["site", "element_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="element_delete",
            description=(
                "Delete an element (DELETE /elements/{id}). Voog returns "
                "204. Requires force=true; without it the call is "
                "rejected. Run elements_list first to confirm the id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "element_id": {
                        "type": "integer",
                        "description": "Voog element id (from elements_list)",
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
                "required": ["site", "element_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="element_move",
            description=(
                "Re-order or re-parent an element instance "
                "(`PUT /elements/{element_id}/move`). Inputs travel as "
                "QUERY-STRING params per Voog docs (mirrors node_move). "
                "All params optional; supply at least one of `page_id`, "
                "`before`, or `after`. `page_id` = new parent page id "
                "(integer); `before` / `after` = existing element id "
                "for positional placement on current or new parent page. "
                "SCOPE NOTE: this operates on element INSTANCES inside a "
                "definition, not on element_definitions (the schema). "
                "element_definition mutations remain passthrough — "
                "different resource. Use elements_list to find element "
                "ids; use element_definitions_list for schema discovery. "
                "Voog docs: "
                "https://www.voog.com/developers/api/resources/elements"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "element_id": {
                        "type": "integer",
                        "description": "Voog element id (from elements_list)",
                    },
                    "page_id": {
                        "type": "integer",
                        "description": (
                            "New parent PAGE id. Omit to keep the "
                            "current parent page. Note: parent is a "
                            "PAGE id (`page.id` from pages_list), "
                            "not an element id."
                        ),
                    },
                    "before": {
                        "type": "integer",
                        "description": (
                            "Existing ELEMENT id; the moved element "
                            "is placed before it. Mutually exclusive "
                            "with `after`."
                        ),
                    },
                    "after": {
                        "type": "integer",
                        "description": (
                            "Existing ELEMENT id; the moved element "
                            "is placed after it. Mutually exclusive "
                            "with `before`."
                        ),
                    },
                },
                "required": ["site", "element_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def _elements_list(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    for int_field in ("page_id", "language_id", "element_definition_id"):
        val = arguments.get(int_field)
        if val is not None:
            err = require_int(int_field, val, tool_name="elements_list")
            if err:
                return error_response(err)
    filters = arguments.get("filters")
    err = validate_filters(filters, resource="element", tool_name="elements_list")
    if err:
        return error_response(err)
    params = build_list_params(arguments, plain=_ELEMENTS_LIST_FILTERS)
    if filters:
        params.update(filters)
    try:
        elements = client.get_all("/elements", params=params or None)
        # PR #116 review: thread include_values through to the projection
        # so the tool description's promise ("include the values hash in
        # the projection") is honoured end-to-end. The same flag is also
        # forwarded to Voog as `?include_values=true` (in `params` above)
        # so Voog populates `values` server-side; without that, even a
        # values-aware projection would have nothing to surface.
        simplified = simplify_elements(
            elements,
            include_values=bool(arguments.get("include_values")),
        )
        return success_response(
            simplified,
            summary=f"🧩 {len(simplified)} elements",
        )
    except Exception as e:
        return error_response(f"elements_list failed: {e}")


def _element_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    element_id = arguments.get("element_id")
    err = require_int("element_id", element_id, tool_name="element_get")
    if err:
        return error_response(err)
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


_ELEMENT_CREATE_FIELDS = (
    "element_definition_id",
    "element_definition_title",
    "page_id",
    "title",
    "path",
    "values",
)


def _element_create(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    page_id = arguments.get("page_id")
    title = arguments.get("title") or ""
    err = require_int("page_id", page_id, tool_name="element_create")
    if err:
        return error_response(err)
    if not title.strip():
        return error_response("element_create: title is required")
    if (
        arguments.get("element_definition_id") is None
        and not (arguments.get("element_definition_title") or "").strip()
    ):
        return error_response(
            "element_create: supply element_definition_id (preferred) or element_definition_title"
        )
    def_id = arguments.get("element_definition_id")
    if def_id is not None:
        err = require_int("element_definition_id", def_id, tool_name="element_create")
        if err:
            return error_response(err)

    body: dict = {}
    for key in _ELEMENT_CREATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post("/elements", body)
        new_id = result.get("id") if isinstance(result, dict) else None
        return success_response(
            result,
            summary=(
                f"🧩 element created (id={new_id}, title={title!r})"
                if new_id
                else f"🧩 element created (title={title!r})"
            ),
        )
    except Exception as e:
        return error_response(f"element_create failed: {e}")


_ELEMENT_UPDATE_FIELDS = ("title", "path", "values")


def _element_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    element_id = arguments.get("element_id")
    err = require_int("element_id", element_id, tool_name="element_update")
    if err:
        return error_response(err)
    body: dict = {}
    for key in _ELEMENT_UPDATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(
            "element_update: supply at least one of "
            f"{list(_ELEMENT_UPDATE_FIELDS)} besides element_id"
        )
    try:
        result = client.put(f"/elements/{element_id}", body)
        return success_response(
            result,
            summary=f"🧩 element {element_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"element_update id={element_id} failed: {e}")


def _element_delete(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    element_id = arguments.get("element_id")
    err = require_int("element_id", element_id, tool_name="element_delete")
    if err:
        return error_response(err)
    err = require_force(
        arguments,
        tool_name="element_delete",
        target_desc=f"element {element_id}",
        hint="Run elements_list first to confirm.",
    )
    if err:
        return error_response(err)
    try:
        client.delete(f"/elements/{element_id}")
        return success_response(
            {"deleted": {"element_id": element_id}},
            summary=f"🗑️  element {element_id} deleted",
        )
    except Exception as e:
        return error_response(f"element_delete id={element_id} failed: {e}")


_ELEMENT_MOVE_FIELDS = ("page_id", "before", "after")


def _element_move(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    element_id = arguments.get("element_id")
    err = require_int("element_id", element_id, tool_name="element_move")
    if err:
        return error_response(err)
    params: dict = {}
    for key in _ELEMENT_MOVE_FIELDS:
        val = arguments.get(key)
        if val is not None:
            err = require_int(key, val, tool_name="element_move")
            if err:
                return error_response(err)
            params[key] = val
    if not params:
        return error_response("element_move: supply at least one of page_id / before / after")
    if "before" in params and "after" in params:
        return error_response(
            "element_move: `before` and `after` are mutually exclusive — pick one"
        )
    try:
        result = client.put(f"/elements/{element_id}/move", params=params)
        return success_response(
            result,
            summary=f"🧩 element {element_id} moved: {sorted(params.keys())}",
        )
    except Exception as e:
        return error_response(f"element_move id={element_id} failed: {e}")


_DISPATCH = {
    "elements_list": _elements_list,
    "element_get": _element_get,
    "element_definitions_list": _element_definitions_list,
    "element_create": _element_create,
    "element_update": _element_update,
    "element_delete": _element_delete,
    "element_move": _element_move,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
