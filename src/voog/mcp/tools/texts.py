"""MCP tools for editing page/article text content bodies and content areas.

Three tools — all hit Admin API:

  - ``text_get``         — GET /texts/{id} (read-only)
  - ``text_update``      — PUT /texts/{id} {"body": ...}
  - ``page_add_content`` — POST /pages/{id}/contents to materialise a
                            content area on a fresh page (Voog returns []
                            from /contents until edit-mode opens the page)

Skill-memory rules captured here:
  - Page text bodies are nested in `text` objects; you cannot PUT body
    via /pages/{id}. Walk pages → contents → texts.
  - Default content area name is 'body' (matches an unnamed
    `{% content %}` Liquid tag). Named areas (`{% content name="gallery_1" %}`)
    require name='gallery_1'.
  - content_type defaults to 'text'; 'gallery', 'form', 'content_partial',
    'buy_button', 'code' are also valid (Voog Contents API).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

VALID_CONTENT_TYPES = (
    "text",
    "gallery",
    "form",
    "content_partial",
    "buy_button",
    "code",
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="text_get",
            description=(
                "Get a text resource by id (GET /texts/{id}). Texts hold "
                "the body of `text`-type content areas. Find the text_id "
                "via voog://{site}/pages/{page_id}/contents → text.id. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "text_id": {"type": "integer"},
                },
                "required": ["site", "text_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="text_update",
            description=(
                "Update a text body (PUT /texts/{id} {body}). body is the "
                "raw HTML rendered into the page where the matching "
                "`{% content %}` Liquid tag lives. Reversible by calling "
                "again with the previous body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "text_id": {"type": "integer"},
                    "body": {
                        "type": "string",
                        "description": "Raw HTML for the content area",
                    },
                },
                "required": ["site", "text_id", "body"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_add_content",
            description=(
                "Create a content area + linked text on a page "
                "(POST /pages/{id}/contents). Use this on freshly-created "
                "pages where /contents returns [] until the admin UI's "
                "edit-mode opens the page. name must match the layout's "
                "{% content %} tag — default 'body' for unnamed, "
                "'gallery_1' for named. content_type defaults to 'text'; "
                "valid values: text, gallery, form, content_partial, "
                "buy_button, code."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "name": {
                        "type": "string",
                        "description": (
                            "Content area name (default 'body'; named areas "
                            "match {% content name=\"...\" %})"
                        ),
                        "default": "body",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": list(VALID_CONTENT_TYPES),
                        "default": "text",
                    },
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "text_get":
        text_id = arguments.get("text_id")
        try:
            return success_response(client.get(f"/texts/{text_id}"))
        except Exception as e:
            return error_response(f"text_get id={text_id} failed: {e}")

    if name == "text_update":
        text_id = arguments.get("text_id")
        body = arguments.get("body")
        if body is None:
            return error_response("text_update: body is required")
        try:
            result = client.put(f"/texts/{text_id}", {"body": body})
            return success_response(
                result,
                summary=f"📝 text {text_id} body updated ({len(body)} chars)",
            )
        except Exception as e:
            return error_response(f"text_update id={text_id} failed: {e}")

    if name == "page_add_content":
        page_id = arguments.get("page_id")
        area_name = arguments.get("name") or "body"
        content_type = arguments.get("content_type") or "text"
        if content_type not in VALID_CONTENT_TYPES:
            return error_response(
                f"page_add_content: content_type must be one of "
                f"{VALID_CONTENT_TYPES} (got {content_type!r})"
            )
        try:
            result = client.post(
                f"/pages/{page_id}/contents",
                {"name": area_name, "content_type": content_type},
            )
            return success_response(
                result,
                summary=(
                    f"➕ page {page_id} content area "
                    f"{area_name!r} ({content_type}) added → "
                    f"id={result.get('id')}"
                ),
            )
        except Exception as e:
            return error_response(
                f"page_add_content page={page_id} failed: {e}"
            )

    return error_response(f"Unknown tool: {name}")
