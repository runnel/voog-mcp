"""voog search — full-text search across published site content.

Mirror of the MCP tool `voog_search`. Indexing must be on; output
explains the indexing-off case explicitly.
"""

from __future__ import annotations

import sys

from voog.client import VoogClient

_SCOPE_VALUES = ("pages", "articles", "elements", "products", "all")


def add_arguments(subparsers):
    p = subparsers.add_parser(
        "search",
        help="Full-text search across published site content",
    )
    p.add_argument("q", help="Query string")
    p.add_argument(
        "--scope",
        choices=list(_SCOPE_VALUES),
        default="all",
        help="Narrow results to one resource kind (default: all)",
    )
    p.add_argument(
        "--language-code",
        default=None,
        dest="language_code",
        help="ISO 639-1 code to restrict the search to",
    )
    p.add_argument(
        "--per-page",
        type=int,
        default=None,
        dest="per_page",
        help="Result cap (Voog default 25)",
    )
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    params: dict = {"q": args.q}
    if args.scope != "all":
        params["scope"] = args.scope
    if args.language_code:
        params["language_code"] = args.language_code
    if args.per_page is not None:
        params["per_page"] = args.per_page
    try:
        hits = client.get("/search", params=params)
    except Exception as e:
        sys.stderr.write(f"error: search failed: {e}\n")
        return 1
    if not isinstance(hits, list):
        sys.stderr.write(f"error: unexpected response shape: {type(hits).__name__}\n")
        return 1
    if not hits:
        # Sentinel parity with MCP — surface MD5 indexing-off when applicable.
        try:
            pages = client.get("/pages", params={"per_page": 1})
            if isinstance(pages, list) and pages:
                sentinel_title = (pages[0] or {}).get("title")
                if sentinel_title and sentinel_title.strip():
                    sentinel_hits = client.get("/search", params={"q": sentinel_title})
                    if isinstance(sentinel_hits, list) and len(sentinel_hits) == 0:
                        print(
                            "No hits — and the indexing-off sentinel also "
                            "returned zero. Site indexing appears to be "
                            "disabled. Use `voog pages` / `voog articles` "
                            "for content discovery instead."
                        )
                        return 0
        except Exception:
            pass
        print(f"No hits for {args.q!r} (scope={args.scope}).")
        return 0
    counts: dict = {}
    for h in hits:
        kind = h.get("kind") or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    summary = ", ".join(f"{cnt} {k}" for k, cnt in sorted(counts.items()))
    print(f"{len(hits)} hits ({summary}):")
    for h in hits:
        kind = h.get("kind", "?")
        hid = h.get("id", "?")
        title = (h.get("title") or "").strip()[:60]
        path = h.get("path") or ""
        print(f"  [{kind:<8}] id={hid:<6} {title:<60} {path}")
    return 0
