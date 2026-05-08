"""MCP tools for Voog blog articles — list, get, create, update, publish, delete.

Six tools — all hit Admin API ``/articles``:

  - ``articles_list``    — read-only, simplified projection
  - ``article_get``      — read-only, full article object
  - ``article_create``   — POST /articles (idempotent only if you supply
                            a unique title; Voog auto-suffixes path)
  - ``article_update``   — PUT /articles/{id} (uses autosaved_* per skill)
  - ``article_publish``  — convenience: re-PUT autosaved_* + publishing:true
  - ``article_delete``   — DELETE /articles/{id} (requires force=true)

Skill-memory rules captured in code:
  - article.body / article.title / article.excerpt are read-only — write
    to autosaved_body / autosaved_title / autosaved_excerpt.
  - To publish: send ALL autosaved_* + publishing:true in a single PUT
    so values copy to published fields atomically.
  - article.description ≠ article.excerpt; description is meta-description
    and stays as 'description' (not autosaved_description).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog._payloads import build_article_payload
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import _validate_data_key, strip_site
from voog.projections import simplify_articles

_ARTICLES_PLAIN_PARAMS = ("page_id", "language_code", "language_id", "tag")


def _build_articles_list_params(arguments: dict) -> dict | None:
    """Translate tool args to Voog query params. Returns None when no
    filters are set, so the caller falls through to the unparameterised
    `client.get_all("/articles")` shape."""
    params: dict = {}
    for arg_key in _ARTICLES_PLAIN_PARAMS:
        if arg_key in arguments:
            params[arg_key] = arguments[arg_key]
    if "sort" in arguments:
        params["s"] = arguments["sort"]
    return params or None


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="articles_list",
            description=(
                "List blog articles on the Voog site (simplified: id, title, "
                "path, public_url, published, published_at, updated_at, "
                "created_at, language_code, page_id). All filters optional. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {
                        "type": "integer",
                        "description": "Filter to a specific blog page id",
                    },
                    "language_code": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Filter by language code (e.g. 'et', 'en')",
                    },
                    "language_id": {
                        "type": "integer",
                        "description": "Filter by language id (use language_code for the human-readable form)",
                    },
                    "tag": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Filter to articles tagged with this label",
                    },
                    "sort": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Voog sort string: '<object>.<attr>.<$asc|$desc>'. "
                            "Example: 'article.created_at.$desc'."
                        ),
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
            name="article_get",
            description=(
                "Get full article details by id (title, path, body, "
                "autosaved_*, published_at, language, page, data, image, "
                "tags). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_create",
            description=(
                "Create a new blog article. Required: page_id (the parent "
                "blog page), title. Optional: body (HTML), excerpt, "
                "description (meta), path (auto from title if omitted), "
                "image_id, tag_names (array), data (custom dict), publish "
                "(default false). Title and body go to autosaved_* fields "
                "per Voog convention; if publish=true, publishing:true is "
                "set so values copy to published fields atomically. NOT "
                "idempotent — repeat calls create multiple articles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {
                        "type": "integer",
                        "description": "Parent blog page id",
                    },
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": (
                            "Meta description (rendered as og_description in "
                            "Voog Liquid). Distinct from excerpt — excerpt "
                            "goes to listings/RSS, description goes to <meta>."
                        ),
                    },
                    "path": {"type": "string"},
                    "image_id": {
                        "type": "integer",
                        "description": "Asset id (must be image content type)",
                    },
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data": {"type": "object"},
                    "publish": {"type": "boolean", "default": False},
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
            name="article_update",
            description=(
                "Update an existing article. Title/body/excerpt go to "
                "autosaved_* per Voog convention (the public fields are "
                "read-only — call article_publish to push autosaved → "
                "published). description/path/image_id/tag_names/data are "
                "non-autosaved fields and update directly. At least one "
                "field must be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "description": {"type": "string"},
                    "path": {"type": "string"},
                    "image_id": {"type": "integer"},
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data": {"type": "object"},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_publish",
            description=(
                "Publish an article. Voog only copies autosaved_* → "
                "published fields when publishing:true is sent in the SAME "
                "PUT as the autosaved values — that's why this needs a "
                "separate tool rather than a `publish` flag on "
                "article_update.\n\n"
                "Two modes:\n"
                "  1. FAST PATH (recommended) — pass ALL THREE "
                "autosaved_title, autosaved_body, autosaved_excerpt args. "
                "Tool issues a single PUT atomically; no race window.\n"
                "  2. FALLBACK — pass none of them. Tool does GET to "
                "fetch current autosaved_* values then PUTs them back "
                "with publishing:true. There is a small race window "
                "between the GET and the PUT — if the article is edited "
                "concurrently, the publish may capture a stale snapshot.\n\n"
                "Mixed (some autosaved_* provided, some not) is rejected — "
                "the caller must be explicit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "autosaved_title": {
                        "type": "string",
                        "description": (
                            "Optional. If all three autosaved_* args are "
                            "supplied, the tool skips the GET and PUTs "
                            "directly (no race window). If none are "
                            "supplied, the tool falls back to GET+PUT."
                        ),
                    },
                    "autosaved_body": {
                        "type": "string",
                        "description": (
                            "Optional. See autosaved_title — must be "
                            "supplied together with the other two "
                            "autosaved_* args, or omitted entirely."
                        ),
                    },
                    "autosaved_excerpt": {
                        "type": "string",
                        "description": (
                            "Optional. See autosaved_title — must be "
                            "supplied together with the other two "
                            "autosaved_* args, or omitted entirely."
                        ),
                    },
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_delete",
            description=(
                "Delete an article. IRREVERSIBLE — Voog does not retain "
                "deleted articles. Requires force=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="article_set_data",
            description=(
                "Set a single article.data.<key> value (PUT /articles/{id}/data/{key}). "
                "To delete a key use article_delete_data. "
                "Keys starting with 'internal_' are server-protected and "
                "rejected client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array"],
                        "description": (
                            "New value for article.data.<key>. Any JSON value "
                            "EXCEPT null — to remove a key, use "
                            "article_delete_data instead. Nested objects and "
                            "arrays are stored as-is and round-tripped on read."
                        ),
                    },
                },
                "required": ["site", "article_id", "key", "value"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_delete_data",
            description=(
                "Delete a single article.data.<key> (DELETE /articles/{id}/data/{key}). "
                "IRREVERSIBLE — the key is removed permanently. "
                "Requires force=true; without it the call is rejected. "
                "Keys starting with 'internal_' are server-protected and "
                "rejected client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "key": {"type": "string"},
                    "force": {
                        "type": "boolean",
                        "description": "Must be true to actually perform the delete. Defaults to false (defensive opt-in).",
                        "default": False,
                    },
                },
                "required": ["site", "article_id", "key"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)


def _articles_list(arguments: dict, client: VoogClient):
    params = _build_articles_list_params(arguments)
    try:
        if params:
            articles = client.get_all("/articles", params=params)
        else:
            articles = client.get_all("/articles")
        simplified = simplify_articles(articles)
        return success_response(simplified, summary=f"📝 {len(simplified)} articles")
    except Exception as e:
        return error_response(f"articles_list failed: {e}")


def _article_get(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    try:
        article = client.get(f"/articles/{article_id}")
        return success_response(article)
    except Exception as e:
        return error_response(f"article_get id={article_id} failed: {e}")


def _article_create(arguments: dict, client: VoogClient):
    page_id = arguments.get("page_id")
    title = arguments.get("title") or ""
    if not isinstance(page_id, int):
        return error_response("article_create: page_id must be an integer")
    if not title.strip():
        return error_response("article_create: title must be non-empty")

    body = build_article_payload(arguments, include_publish=True)
    body["page_id"] = page_id  # POST-only

    try:
        result = client.post("/articles", body)
        return success_response(
            result,
            summary=f"📝 article {result.get('id')} created (page {page_id})",
        )
    except Exception as e:
        return error_response(f"article_create failed: {e}")


def _article_update(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    body = build_article_payload(arguments)
    if not body:
        return error_response(
            "article_update: at least one field (title, body, excerpt, "
            "description, path, image_id, tag_names, data) must be set"
        )
    try:
        result = client.put(f"/articles/{article_id}", body)
        return success_response(
            result,
            summary=f"📝 article {article_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"article_update id={article_id} failed: {e}")


def _article_publish(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    autosaved_keys = ("autosaved_title", "autosaved_body", "autosaved_excerpt")
    provided = {k: arguments[k] for k in autosaved_keys if k in arguments}

    # Mixed (some provided, some not) is ambiguous — force the caller to
    # be explicit. Either pass all three (fast path, no race) or none
    # (fallback to GET+PUT, race window documented in tool description).
    if provided and len(provided) != len(autosaved_keys):
        missing = [k for k in autosaved_keys if k not in provided]
        return error_response(
            "article_publish: when passing autosaved_* arguments, ALL "
            f"three are required (missing: {missing}). Either pass all "
            "of autosaved_title/autosaved_body/autosaved_excerpt to skip "
            "the GET and publish atomically, or pass none of them to "
            "fall back to GET+PUT."
        )

    if provided:
        # Fast path: caller supplied everything; one PUT, no race window.
        body = {"publishing": True, **provided}
    else:
        # Fallback: GET current autosaved_* values, then PUT them back.
        # There is a race window between GET and PUT — concurrent edits
        # may be missed by the publish.
        try:
            article = client.get(f"/articles/{article_id}")
        except Exception as e:
            return error_response(f"article_publish: GET {article_id} failed: {e}")

        if all(article.get(k) is None for k in autosaved_keys):
            return error_response(
                f"article_publish: article {article_id} has no autosaved values to "
                "publish — autosaved_title, autosaved_body, and autosaved_excerpt "
                "are all null. Call article_update first to set the content, then "
                "publish."
            )

        body = {"publishing": True}
        for key in autosaved_keys:
            if article.get(key) is not None:
                body[key] = article[key]

    try:
        result = client.put(f"/articles/{article_id}", body)
        return success_response(
            result,
            summary=f"📢 article {article_id} published",
        )
    except Exception as e:
        return error_response(f"article_publish id={article_id} failed: {e}")


def _article_delete(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    if not arguments.get("force"):
        return error_response(
            f"article_delete: refusing to delete article {article_id} "
            "without force=true. Voog does not retain deleted articles."
        )
    try:
        client.delete(f"/articles/{article_id}")
        return success_response(
            {"deleted": article_id},
            summary=f"🗑️  article {article_id} deleted",
        )
    except Exception as e:
        return error_response(f"article_delete id={article_id} failed: {e}")


def _article_set_data(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    article_id = arguments.get("article_id")
    key = arguments.get("key") or ""
    value = arguments.get("value")

    err = _validate_data_key(key, tool_name="article_set_data")
    if err:
        return error_response(err)
    try:
        result = client.put(f"/articles/{article_id}/data/{key}", {"value": value})
        return success_response(
            result,
            summary=f"📝 article {article_id} data.{key} set",
        )
    except Exception as e:
        return error_response(f"article_set_data article={article_id} key={key!r} failed: {e}")


def _article_delete_data(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    article_id = arguments.get("article_id")
    key = arguments.get("key") or ""
    force = bool(arguments.get("force"))

    err = _validate_data_key(key, tool_name="article_delete_data")
    if err:
        return error_response(err)
    if not force:
        return error_response(
            f"article_delete_data: refusing to delete article {article_id} data.{key!r} without force=true. "
            "Set force=true after confirming the deletion is intentional."
        )
    try:
        client.delete(f"/articles/{article_id}/data/{key}")
        return success_response(
            {"deleted": {"article_id": article_id, "key": key}},
            summary=f"🗑️  article {article_id} data.{key} deleted",
        )
    except Exception as e:
        return error_response(f"article_delete_data article={article_id} key={key!r} failed: {e}")


_DISPATCH = {
    "articles_list": _articles_list,
    "article_get": _article_get,
    "article_create": _article_create,
    "article_update": _article_update,
    "article_publish": _article_publish,
    "article_delete": _article_delete,
    "article_set_data": _article_set_data,
    "article_delete_data": _article_delete_data,
}
