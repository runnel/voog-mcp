"""MCP tool for Voog admin search.

One tool — `voog_search` — wrapping `GET /admin/api/search`.

Site indexing must be enabled in Voog Admin → SEO settings for this
endpoint to return useful results. The index covers PUBLIC content
only and refreshes hourly — draft pages and freshly edited content
will not surface here. For draft discovery use the typed list filters
(`pages_list(filters=...)`, `articles_list(...)`, `text_get(...)`).

MD5 — indexing-off detection. When Voog returns zero results, we
follow up with a sentinel query: fetch one known-existing page title
via `pages_list(per_page=1)` and search for that. If the sentinel
also returns zero hits, indexing is almost certainly disabled and
we surface a clear message rather than silently returning empty.
The sentinel is gated to zero-result responses only — adds one
extra round-trip in the rare failure path, none in the happy path.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

_SCOPE_VALUES = ("pages", "articles", "elements", "products", "all")

_INDEXING_OFF_HINT = (
    "Site indexing appears to be disabled — full-text search returns "
    "no results for any query, including a sentinel against a known "
    "page title. Enable indexing in Voog Admin → SEO settings, or use "
    "`pages_list(filters=...)` / `articles_list(...)` / `text_get(...)` "
    "for content discovery (these query the typed resources directly "
    "and do not depend on the search index)."
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="voog_search",
            description=(
                "Full-text search across the site's published content "
                "(`GET /admin/api/search`). Returns hits across pages, "
                "articles, elements, and products. Use `scope` to narrow "
                "the search to one kind. Indexing is hourly and covers "
                "PUBLIC content only — fresh edits and draft pages will "
                "not appear here. For draft / freshly-edited discovery, "
                "use `pages_list(filters=...)`, `articles_list(...)`, or "
                "`text_get(...)` instead. If indexing is disabled on the "
                "tenant, this tool detects that via a sentinel query and "
                "explains rather than silently returning zero. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "q": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Query string (free-text, required)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": list(_SCOPE_VALUES),
                        "default": "all",
                        "description": "Narrow results to one resource kind",
                    },
                    "language_code": {
                        "type": "string",
                        "minLength": 2,
                        "description": "ISO 639-1 code (e.g. 'et', 'en') — restrict to one language",
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Result cap (Voog default 25, server-side max 250)",
                    },
                },
                "required": ["site", "q"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def _kind_counts(hits: list) -> dict:
    """Count hits by `kind` field. Defensive against missing/None."""
    counts: dict = {}
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        kind = hit.get("kind") or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _build_params(arguments: dict) -> dict:
    params: dict = {"q": arguments["q"]}
    scope = arguments.get("scope")
    if scope and scope != "all":
        params["scope"] = scope
    if arguments.get("language_code"):
        params["language_code"] = arguments["language_code"]
    if arguments.get("per_page") is not None:
        params["per_page"] = arguments["per_page"]
    return params


def _indexing_appears_disabled(client: VoogClient) -> bool:
    """MD5 sentinel — fetch the first page title and search for it.

    Returns True when the sentinel query also returns zero hits (strong
    signal indexing is off). Returns False on any error or when the
    sentinel succeeds — false positives are worse than missing the
    detection. Network errors propagate to the caller path.
    """
    pages = client.get("/pages", params={"per_page": 1})
    if not isinstance(pages, list) or not pages:
        return False
    sentinel_title = (pages[0] or {}).get("title")
    if not sentinel_title or not sentinel_title.strip():
        return False
    sentinel_hits = client.get("/search", params={"q": sentinel_title})
    if not isinstance(sentinel_hits, list):
        return False
    return len(sentinel_hits) == 0


def _voog_search(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    q = (arguments.get("q") or "").strip()
    if not q:
        return error_response("voog_search: q is required (non-empty query string)")
    scope = arguments.get("scope") or "all"
    if scope not in _SCOPE_VALUES:
        return error_response(
            f"voog_search: scope must be one of {list(_SCOPE_VALUES)} (got {scope!r})"
        )
    params = _build_params({**arguments, "q": q, "scope": scope})
    try:
        hits = client.get("/search", params=params)
    except Exception as e:
        return error_response(f"voog_search failed: {e}")
    if not isinstance(hits, list):
        return error_response(
            f"voog_search: unexpected response shape (expected list, got {type(hits).__name__})"
        )
    counts = _kind_counts(hits)
    total = len(hits)
    if total == 0:
        try:
            if _indexing_appears_disabled(client):
                return success_response(
                    {"hits": [], "indexing_disabled": True, "hint": _INDEXING_OFF_HINT},
                    summary=f"🔍 0 hits for {q!r} — {_INDEXING_OFF_HINT}",
                )
        except Exception:
            pass
        return success_response(
            {"hits": [], "kind_counts": {}},
            summary=f"🔍 0 hits for {q!r} (scope={scope})",
        )
    summary_parts = [f"{cnt} {kind}" for kind, cnt in sorted(counts.items())]
    summary = f"🔍 {total} hits for {q!r} (scope={scope}): " + ", ".join(summary_parts)
    return success_response({"hits": hits, "kind_counts": counts}, summary=summary)


_DISPATCH = {
    "voog_search": _voog_search,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
