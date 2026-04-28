"""MCP resources for Voog blog articles.

Phase D resource group covering two URI shapes:

  - ``voog://articles``        — list all articles (id, title, path, public_url,
                                  published, published_at, updated_at, created_at,
                                  language_code, page_id — body field stripped;
                                  bodies live at ``voog://articles/{id}``)
  - ``voog://articles/{id}``   — article body (HTML) as ``text/html``

The single-article URI returns ``mime_type="text/html"`` because the value is
the rendered HTML body of the post, not JSON — constructed locally rather
than via :func:`json_response` for that reason. The list URI returns
``application/json`` via the shared helper.

Pattern mirrors :mod:`voog_mcp.resources.layouts`: ``URI_PREFIX`` constant,
exact-or-slashed-sub-path :func:`matches`, strict :func:`parse_id`, errors
propagate to the server layer (no wrapping into MCP error responses).
"""
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.resources._helpers import json_response, parse_id, prefix_matcher


URI_PREFIX = "voog://articles"
matches = prefix_matcher(URI_PREFIX)


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_PREFIX,
            name="Articles",
            description=(
                "All blog articles on the Voog site (simplified: id, title, path, "
                "public_url, published, published_at, updated_at, created_at, "
                "language_code, page_id — without bodies). "
                "Single article body (rendered HTML) at voog://articles/{id} as text/html."
            ),
            mimeType="application/json",
        ),
    ]


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        articles = client.get_all("/articles")
        return json_response(_simplify_articles(articles))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"articles resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        article_id = parse_id(parts[0], uri, group_name="articles")
        article = client.get(f"/articles/{article_id}")
        body = article.get("body") or ""
        return [
            ReadResourceContents(
                content=body,
                mime_type="text/html",
            )
        ]

    raise ValueError(f"articles resource: unsupported URI {uri!r}")


def _simplify_articles(articles: list) -> list:
    """Project articles list to lightweight metadata (no body field).

    The Voog ``/articles`` list endpoint already omits ``body`` from each item,
    but defensive trimming guarantees the projection even if the API ever
    starts returning bodies (which would balloon list payloads).
    """
    simplified = []
    for article in articles:
        lang = article.get("language") or {}
        page = article.get("page") or {}
        simplified.append({
            "id": article.get("id"),
            "title": article.get("title"),
            "path": article.get("path"),
            "public_url": article.get("public_url"),
            "published": article.get("published"),
            "published_at": article.get("published_at"),
            "updated_at": article.get("updated_at"),
            "created_at": article.get("created_at"),
            "language_code": lang.get("code"),
            "page_id": page.get("id"),
        })
    return simplified
