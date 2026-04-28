"""MCP resources for Voog blog articles.

Two URI shapes:

  - ``voog://{site}/articles``        — list all articles (id, title, path, public_url,
                                         published, published_at, updated_at, created_at,
                                         language_code, page_id — body field stripped)
  - ``voog://{site}/articles/{id}``   — article body (HTML) as ``text/html``
"""
import re

from mcp.types import Resource

from voog.client import VoogClient
from voog.projections import simplify_articles
from voog.mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
    text_response,
)


URI_TEMPLATE = "voog://{site}/articles"
_URI_RE = re.compile(r"^voog://[^/]+/articles(/.*)?$")


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_TEMPLATE]


def matches(uri: str) -> bool:
    return bool(_URI_RE.match(uri))


def _strip_site(uri: str) -> str:
    """voog://stella/articles/42 → /articles/42"""
    rest = uri[len("voog://"):]
    _, _, path = rest.partition("/")
    return "/" + path


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_TEMPLATE,
            name="Articles",
            description=(
                "All blog articles on the Voog site (simplified: id, title, path, "
                "public_url, published, published_at, updated_at, created_at, "
                "language_code, page_id — without bodies). "
                "Single article body (rendered HTML) at voog://{site}/articles/{id} as text/html."
            ),
            mimeType="application/json",
        ),
    ]


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    local = _strip_site(uri)  # e.g. /articles or /articles/42

    if local == "/articles":
        articles = client.get_all("/articles")
        return json_response(simplify_articles(articles))

    if not local.startswith("/articles/"):
        raise ValueError(f"articles resource: unsupported URI {uri!r}")

    sub = local[len("/articles/"):]
    parts = sub.split("/")

    if len(parts) == 1:
        article_id = parse_id(parts[0], uri, group_name="articles")
        article = client.get(f"/articles/{article_id}")
        return text_response(article.get("body") or "", mime_type="text/html")

    raise ValueError(f"articles resource: unsupported URI {uri!r}")
