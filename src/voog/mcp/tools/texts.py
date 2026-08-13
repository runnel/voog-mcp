"""MCP tools for editing page/article text content bodies and content areas.

Four tools — all hit Admin API:

  - ``text_get``            — GET /texts/{id} (read-only)
  - ``text_update``         — PUT /texts/{id} {"body": ...}
  - ``page_add_content``    — POST /pages/{id}/contents to materialise a
                               content area on a fresh page (Voog returns []
                               from /contents until edit-mode opens the page)
  - ``article_add_content`` — the same for blog articles, POST
                               /articles/{id}/contents (issue #140 item 6:
                               seeding an article body previously needed raw
                               API calls because only the page half existed)

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
from voog.mcp.tools._helpers import require_int, strip_site

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
                "buy_button, code.\n\n"
                "By default, the tool first GETs /pages/{id}/contents and "
                "refuses if a content area with the same name already "
                "exists — calling twice with the same name was silently "
                "creating duplicates. To edit the existing area, use "
                "text_update on its text.id. Pass force=true to skip the "
                "pre-check (legitimate use: page templates with multiple "
                "areas sharing the same name)."
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
                            'match {% content name="..." %})'
                        ),
                        "default": "body",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": list(VALID_CONTENT_TYPES),
                        "default": "text",
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Skip the duplicate-name pre-check. Default "
                            "false: the tool refuses to create a second "
                            "area with a name that already exists on the "
                            "page. Set true only when the layout legitimately "
                            "uses repeated names."
                        ),
                        "default": False,
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
        Tool(
            name="article_add_content",
            description=(
                "Create a content area + linked text on a blog article "
                "(POST /articles/{id}/contents) — the article counterpart of "
                "page_add_content, with identical semantics. Use it to seed a "
                "freshly-created article's body or gallery, which /contents "
                "reports as [] until edit-mode opens the article.\n\n"
                "name must match the article layout's {% content %} tag "
                "(default 'body'; named areas match "
                '{% content name="..." %}). content_type defaults to "text"; '
                "valid values: text, gallery, form, content_partial, "
                "buy_button, code.\n\n"
                "Refuses by default if an area with the same name already "
                "exists, since calling twice otherwise creates duplicates. "
                "Repeated names ARE legitimate in some article layouts "
                "(observed live: two 'text-images' areas on one article) — "
                "pass force=true for those."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "name": {
                        "type": "string",
                        "description": (
                            "Content area name (default 'body'; named areas "
                            'match {% content name="..." %})'
                        ),
                        "default": "body",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": list(VALID_CONTENT_TYPES),
                        "default": "text",
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Skip the duplicate-name pre-check. Default "
                            "false: the tool refuses to create a second "
                            "area with a name that already exists on the "
                            "article."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset({"text_get", "text_update", "page_add_content", "article_add_content"})


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")

    # S9: tag every HTTP request inside this dispatch with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring.
    with client.with_tool(name):
        return _dispatch(name, arguments, client)


def _dispatch(name: str, arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    if name == "text_get":
        text_id = arguments.get("text_id")
        err = require_int("text_id", text_id, tool_name="text_get")
        if err:
            return error_response(err)
        try:
            return success_response(client.get(f"/texts/{text_id}"))
        except Exception as e:
            return error_response(f"text_get id={text_id} failed: {e}")

    if name == "text_update":
        text_id = arguments.get("text_id")
        err = require_int("text_id", text_id, tool_name="text_update")
        if err:
            return error_response(err)
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
        return _add_content(
            arguments,
            client,
            tool_name="page_add_content",
            id_field="page_id",
            collection="pages",
            noun="page",
        )

    if name == "article_add_content":
        return _add_content(
            arguments,
            client,
            tool_name="article_add_content",
            id_field="article_id",
            collection="articles",
            noun="article",
        )

    return error_response(f"Unknown tool: {name}")


def _add_content(
    arguments: dict,
    client: VoogClient,
    *,
    tool_name: str,
    id_field: str,
    collection: str,
    noun: str,
) -> list[TextContent] | CallToolResult:
    """POST a content area to a page or an article.

    One implementation for both: Voog's /pages/{id}/contents and
    /articles/{id}/contents behave identically, and the duplicate-name
    pre-check is the part worth not forking (#75 precedent — the page half
    only grew that check after repeat calls silently created duplicates).
    """
    parent_id = arguments.get(id_field)
    err = require_int(id_field, parent_id, tool_name=tool_name)
    if err:
        return error_response(err)
    area_name = arguments.get("name") or "body"
    content_type = arguments.get("content_type") or "text"
    force = bool(arguments.get("force"))
    if content_type not in VALID_CONTENT_TYPES:
        return error_response(
            f"{tool_name}: content_type must be one of {VALID_CONTENT_TYPES} (got {content_type!r})"
        )

    # Default: pre-check that no area with the same name already exists.
    # Calling twice with the same name was silently creating duplicates.
    # Use get_all so a same-name area on a paginated later page can't
    # slip past — Voog's /contents endpoint paginates.
    if not force:
        try:
            existing = client.get_all(f"/{collection}/{parent_id}/contents")
        except Exception as e:
            return error_response(f"{tool_name} {noun}={parent_id} pre-check failed: {e}")
        existing_list = existing if isinstance(existing, list) else []
        for content in existing_list:
            if isinstance(content, dict) and content.get("name") == area_name:
                return error_response(
                    f"{tool_name}: {noun} {parent_id} already has a "
                    f"content area named {area_name!r} (id={content.get('id')}). "
                    "To edit it, use text_update on its text.id. To add "
                    "another area with the same name anyway (e.g. for a "
                    "template with repeated section names), pass force=true."
                )

    try:
        result = client.post(
            f"/{collection}/{parent_id}/contents",
            {"name": area_name, "content_type": content_type},
        )
        return success_response(
            result,
            summary=(
                f"➕ {noun} {parent_id} content area "
                f"{area_name!r} ({content_type}) added → "
                f"id={result.get('id')}"
            ),
        )
    except Exception as e:
        return error_response(f"{tool_name} {noun}={parent_id} failed: {e}")
