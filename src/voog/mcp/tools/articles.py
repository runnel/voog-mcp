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

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_articles


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="articles_list",
            description=(
                "List all blog articles on the Voog site (simplified: id, "
                "title, path, public_url, published, published_at, "
                "updated_at, created_at, language_code, page_id). Read-only."
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
                "Publish an article: GET current autosaved_* values, then "
                "PUT them back together with publishing:true. Voog only "
                "copies autosaved_* → published when publishing:true is "
                "sent in the SAME PUT — that's why this needs a separate "
                "tool rather than just an `publish` flag on article_update."
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
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "articles_list":
        return _articles_list(client)
    if name == "article_get":
        return _article_get(arguments, client)
    if name == "article_create":
        return _article_create(arguments, client)
    if name == "article_update":
        return _article_update(arguments, client)
    if name == "article_publish":
        return _article_publish(arguments, client)
    if name == "article_delete":
        return _article_delete(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _articles_list(client: VoogClient):
    try:
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

    body = {
        "page_id": page_id,
        "autosaved_title": title,
    }
    # Use `is not None` (matches article_update) so empty strings/lists are
    # preserved as legitimate "set this field to empty" inputs.
    if arguments.get("body") is not None:
        body["autosaved_body"] = arguments["body"]
    if arguments.get("excerpt") is not None:
        body["autosaved_excerpt"] = arguments["excerpt"]
    if arguments.get("description") is not None:
        body["description"] = arguments["description"]
    if arguments.get("path") is not None:
        body["path"] = arguments["path"]
    if arguments.get("image_id") is not None:
        body["image_id"] = arguments["image_id"]
    if arguments.get("tag_names") is not None:
        body["tag_names"] = arguments["tag_names"]
    if arguments.get("data") is not None:
        body["data"] = arguments["data"]
    if arguments.get("publish"):
        body["publishing"] = True

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
    body: dict = {}

    # Map writeable fields → autosaved_* (or pass-through for non-autosaved).
    if arguments.get("title") is not None:
        body["autosaved_title"] = arguments["title"]
    if arguments.get("body") is not None:
        body["autosaved_body"] = arguments["body"]
    if arguments.get("excerpt") is not None:
        body["autosaved_excerpt"] = arguments["excerpt"]
    if arguments.get("description") is not None:
        body["description"] = arguments["description"]
    if arguments.get("path") is not None:
        body["path"] = arguments["path"]
    if arguments.get("image_id") is not None:
        body["image_id"] = arguments["image_id"]
    if arguments.get("tag_names") is not None:
        body["tag_names"] = arguments["tag_names"]
    if arguments.get("data") is not None:
        body["data"] = arguments["data"]

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
    try:
        article = client.get(f"/articles/{article_id}")
    except Exception as e:
        return error_response(f"article_publish: GET {article_id} failed: {e}")

    autosaved_keys = ("autosaved_title", "autosaved_body", "autosaved_excerpt")
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
