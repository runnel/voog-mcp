"""MCP resources for Voog blog articles.

Two URI shapes:

  - ``voog://articles``        — list all articles (id, title, path, public_url,
                                  published, published_at, updated_at, created_at,
                                  language_code, page_id — body field stripped)
  - ``voog://articles/{id}``   — article body (HTML) as ``text/html``
"""
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.projections import simplify_articles
from voog_mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
    prefix_matcher,
    text_response,
)


URI_PREFIX = "voog://articles"
matches = prefix_matcher(URI_PREFIX)


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_PREFIX]


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


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        articles = client.get_all("/articles")
        return json_response(simplify_articles(articles))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"articles resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        article_id = parse_id(parts[0], uri, group_name="articles")
        article = client.get(f"/articles/{article_id}")
        return text_response(article.get("body") or "", mime_type="text/html")

    raise ValueError(f"articles resource: unsupported URI {uri!r}")
