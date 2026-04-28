"""MCP tools for Voog snapshots — pages and full site backups.

Two filesystem-touching tools:

  - ``pages_snapshot(output_dir)``  — write ``pages.json`` + per-page contents
                                       to disk. Allows overwriting an existing
                                       directory (refresh use case).
  - ``site_snapshot(output_dir)``   — comprehensive read-only backup of every
                                       mutable Voog resource. **Refuses** to
                                       overwrite an existing directory; caller
                                       must pick a fresh location to prevent
                                       mixing old/new state.

v1: synchronous. ``site_snapshot`` on a large site can take 30–60s as it
fetches every list endpoint, every singleton, per-page contents, per-article
details, and per-product details (with translations + variant_types).
Progress notifications deferred to v0.3 per spec § 8.

Pattern note: these are the only Phase C tools that *write* the local
filesystem. Annotations: ``readOnlyHint=False`` (we write disk),
``destructiveHint=False`` (no API mutation; ``site_snapshot``'s refuse-existing
guarantees no data loss), ``idempotentHint=True`` (re-running on the same
site produces equivalent output).
"""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response


# Standard /admin/api/ list endpoints. Each is paginated via client.get_all.
SITE_SNAPSHOT_LIST_ENDPOINTS = [
    "/pages",
    "/articles",
    "/elements",
    "/element_definitions",
    "/layouts",
    "/layout_assets",
    "/languages",
    "/redirect_rules",
    "/nodes",
    "/texts",
    "/content_partials",
    "/tags",
    "/forms",
    "/media_sets",
    "/assets",
    "/webhooks",
]

# Standard /admin/api/ singletons (no list).
SITE_SNAPSHOT_SINGLETONS = ["/site", "/me"]


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="pages_snapshot",
            description=(
                "Backup all pages + per-page contents to JSON files in "
                "output_dir. Creates the directory (and parents) if needed; "
                "overwrites existing pages.json. Lighter than site_snapshot — "
                "use this when you only need page structure and contents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "description": "Absolute path where pages.json + page_{id}_contents.json files are written",
                    },
                },
                "required": ["output_dir"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="site_snapshot",
            description=(
                "Comprehensive read-only backup of every mutable Voog resource: "
                "pages, articles, elements, layouts, layout_assets, languages, "
                "redirect_rules, nodes, texts, content_partials, tags, forms, "
                "media_sets, assets, webhooks, site, me, products (with "
                "translations + variant_types), per-page contents, per-article "
                "details, per-product details, and rendered HTML samples for "
                "VoogStyle capture. REFUSES to overwrite an existing directory — "
                "pick a fresh location. REQUIRED pre-flight before any risky "
                "operation: layout rename, mass push, layout swap, VoogStyle "
                "template push, page_delete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "description": "Absolute path to a fresh (non-existing) directory for the snapshot",
                    },
                },
                "required": ["output_dir"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


async def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}

    if name == "pages_snapshot":
        return _pages_snapshot(arguments, client)

    if name == "site_snapshot":
        return _site_snapshot(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _snapshot_filename_for(endpoint: str) -> str:
    """`/redirect_rules` → `redirect_rules.json`."""
    return endpoint.lstrip("/").replace("/", "_") + ".json"


def _format_skip(label: str, exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return f"{label}: endpoint not available (404)"
    if isinstance(exc, urllib.error.HTTPError):
        return f"{label}: HTTP {exc.code} {exc.reason}"
    return f"{label}: {exc}"


def _pages_snapshot(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    output_dir = arguments.get("output_dir") or ""
    if not output_dir:
        return error_response("pages_snapshot: output_dir must be a non-empty string")
    if not Path(output_dir).is_absolute():
        return error_response(
            f"pages_snapshot: output_dir must be an absolute path (got {output_dir!r})"
        )

    out = Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
        pages = client.get_all("/pages")
        _write_json(out / "pages.json", pages)
    except Exception as e:
        return error_response(f"pages_snapshot ebaõnnestus: {e}")

    page_contents_written = 0
    per_page_errors: list = []
    for p in pages:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = client.get(f"/pages/{pid}/contents")
        except Exception as e:
            per_page_errors.append({"page_id": pid, "error": str(e)})
            continue
        _write_json(out / f"page_{pid}_contents.json", contents)
        page_contents_written += 1

    summary = (
        f"📥 pages_snapshot: {len(pages)} pages, "
        f"{page_contents_written} contents files → {output_dir}"
    )
    if per_page_errors:
        summary += f" ({len(per_page_errors)} per-page errors)"

    return success_response(
        {
            "output_dir": output_dir,
            "pages": len(pages),
            "page_contents_written": page_contents_written,
            "per_page_errors": per_page_errors,
        },
        summary=summary,
    )


def _site_snapshot(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    output_dir = arguments.get("output_dir") or ""
    if not output_dir:
        return error_response("site_snapshot: output_dir must be a non-empty string")
    if not Path(output_dir).is_absolute():
        return error_response(
            f"site_snapshot: output_dir must be an absolute path (got {output_dir!r})"
        )

    out = Path(output_dir)
    if out.exists():
        return error_response(
            f"site_snapshot: output_dir {output_dir!r} already exists. "
            "Pick a fresh location to prevent mixing old/new state."
        )

    try:
        out.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return error_response(f"site_snapshot: cannot create {output_dir!r}: {e}")

    files_written = 0
    skipped: list = []
    pages_data: list = []
    articles_data: list = []
    products_data: list = []

    # 1. Standard list endpoints (paginated)
    for endpoint in SITE_SNAPSHOT_LIST_ENDPOINTS:
        filename = _snapshot_filename_for(endpoint)
        try:
            data = client.get_all(endpoint)
        except Exception as e:
            skipped.append({"file": filename, "reason": _format_skip(filename, e)})
            continue
        _write_json(out / filename, data)
        files_written += 1
        if endpoint == "/pages":
            pages_data = data
        elif endpoint == "/articles":
            articles_data = data

    # 2. Singletons
    for endpoint in SITE_SNAPSHOT_SINGLETONS:
        filename = _snapshot_filename_for(endpoint)
        try:
            data = client.get(endpoint)
        except Exception as e:
            skipped.append({"file": filename, "reason": _format_skip(filename, e)})
            continue
        _write_json(out / filename, data)
        files_written += 1

    # 3. Per-page contents
    page_contents_count = 0
    for p in pages_data:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = client.get(f"/pages/{pid}/contents")
        except Exception as e:
            skipped.append({"file": f"page_{pid}_contents.json", "reason": str(e)})
            continue
        _write_json(out / f"page_{pid}_contents.json", contents)
        files_written += 1
        page_contents_count += 1

    # 4. Per-article details
    article_detail_count = 0
    for a in articles_data:
        aid = a.get("id")
        if not aid:
            continue
        try:
            detail = client.get(f"/articles/{aid}")
        except Exception as e:
            skipped.append({"file": f"article_{aid}.json", "reason": str(e)})
            continue
        _write_json(out / f"article_{aid}.json", detail)
        files_written += 1
        article_detail_count += 1

    # 5. Ecommerce: products list + per-product details
    try:
        products_data = client.get_all("/products", base=client.ecommerce_url)
    except Exception as e:
        skipped.append({"file": "products.json", "reason": _format_skip("products.json", e)})
        products_data = []

    if products_data:
        _write_json(out / "products.json", products_data)
        files_written += 1
        for prod in products_data:
            pid = prod.get("id")
            if not pid:
                continue
            try:
                detail = client.get(
                    f"/products/{pid}",
                    base=client.ecommerce_url,
                    params={"include": "variant_types,translations"},
                )
            except Exception as e:
                skipped.append({"file": f"product_{pid}.json", "reason": str(e)})
                continue
            _write_json(out / f"product_{pid}.json", detail)
            files_written += 1

    # 6. Rendered HTML samples for VoogStyle capture (best-effort).
    # Public HTML fetch — no API key needed. Gracefully skipped if the host
    # is unreachable from the MCP server's network.
    rendered_count = 0
    sample_paths = _pick_sample_page_paths(pages_data)
    for path in sample_paths:
        slug = _slugify_path(path)
        url = f"https://{client.host}{path}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 voog-mcp-snapshot/1.0"}
            )
            # Public HTML fetch is unauthenticated and bypasses VoogClient,
            # so it needs its own timeout. 30s is shorter than the API
            # default (60s) — a rendered page that hasn't responded by then
            # is unlikely to ever, and we'd rather skip than hang.
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            skipped.append({
                "file": f"voog_style_rendered_{slug}.html",
                "reason": f"public fetch failed: {e}",
            })
            continue
        (out / f"voog_style_rendered_{slug}.html").write_text(html, encoding="utf-8")
        files_written += 1
        rendered_count += 1

    summary = (
        f"📦 site_snapshot: {files_written} files → {output_dir} "
        f"({len(pages_data)} pages, {len(articles_data)} articles, "
        f"{len(products_data)} products, {rendered_count} rendered HTML)"
    )
    if skipped:
        summary += f" ({len(skipped)} skipped/errored)"

    return success_response(
        {
            "output_dir": output_dir,
            "files_written": files_written,
            "pages_count": len(pages_data),
            "articles_count": len(articles_data),
            "products_count": len(products_data),
            "page_contents_written": page_contents_count,
            "article_details_written": article_detail_count,
            "rendered_html_written": rendered_count,
            "skipped": skipped,
        },
        summary=summary,
    )


def _slugify_path(path: str) -> str:
    """URL path → filename slug. Empty/`/` → `home`."""
    p = (path or "").strip("/")
    if not p:
        return "home"
    cleaned = re.sub(r"[^a-z0-9-]+", "-", p.lower()).strip("-")
    return cleaned or "home"


def _pick_sample_page_paths(pages: list, max_samples: int = 3) -> list:
    """Pick representative URL paths to render for VoogStyle capture.

    Prefers front page + variety across content_types, skips hidden pages.
    Returns list of URL paths starting with "/".
    """
    if not pages:
        return []
    visible = [p for p in pages if not p.get("hidden")] or list(pages)

    seen_urls: set = set()
    seen_cts: set = set()
    picks: list = []

    # Front page (empty path)
    for p in visible:
        if (p.get("path") or "").strip("/") == "":
            picks.append("/")
            seen_urls.add("/")
            seen_cts.add(p.get("content_type") or "default")
            break

    # One per new content_type
    by_ct: dict = {}
    for p in visible:
        if (p.get("path") or "").strip("/") == "":
            continue
        ct = p.get("content_type") or "default"
        by_ct.setdefault(ct, []).append(p)
    for ct, items in sorted(by_ct.items()):
        if len(picks) >= max_samples:
            break
        if ct in seen_cts:
            continue
        url = "/" + (items[0].get("path") or "").strip("/")
        if url not in seen_urls:
            seen_urls.add(url)
            seen_cts.add(ct)
            picks.append(url)

    # Fill remaining slots
    for p in visible:
        if len(picks) >= max_samples:
            break
        url = "/" + (p.get("path") or "").strip("/")
        if url == "/" or url in seen_urls:
            continue
        seen_urls.add(url)
        picks.append(url)

    return picks[:max_samples]
