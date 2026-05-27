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

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp.types import CallToolResult, TextContent, Tool

from voog import __version__ as _voog_version
from voog._concurrency import parallel_map, propagate_tool_context
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site, validate_output_dir, write_json
from voog.projections import LAYOUTS_INCLUDE_BODY, PRODUCTS_DETAIL_INCLUDE

# Standard /admin/api/ list endpoints. Each is paginated via client.get_all.
# Shape: ``(endpoint, params_or_None)`` tuples. ``params=None`` means the
# default (no query-string params beyond pagination). Endpoints that need
# a specific ``?include=`` or other modifier carry their params here so the
# constant represents *all* list endpoints in the snapshot — no sibling
# constants, no separate post-loop fetches, no drift trap for CLI vs MCP
# consumers (v1.4 design fix surfacing during PR #123 review).
SITE_SNAPSHOT_LIST_ENDPOINTS: list[tuple[str, dict | None]] = [
    ("/pages", None),
    ("/articles", None),
    ("/elements", None),
    ("/element_definitions", None),
    # S1 (v1.4): /layouts list includes bodies inline so the snapshot's
    # layouts.json is restore-ready without per-id fetches.
    ("/layouts", LAYOUTS_INCLUDE_BODY),
    ("/layout_assets", None),
    ("/languages", None),
    ("/redirect_rules", None),
    ("/nodes", None),
    ("/texts", None),
    ("/content_partials", None),
    ("/tags", None),
    ("/forms", None),
    ("/media_sets", None),
    ("/assets", None),
    ("/webhooks", None),
]

# Standard /admin/api/ singletons (no list).
SITE_SNAPSHOT_SINGLETONS = ["/site", "/me"]


@dataclass
class _Manifest:
    """Snapshot manifest — written to ``<output_dir>/_meta.json``.

    Built progressively during ``_site_snapshot``: every list endpoint,
    singleton, per-page contents, per-article detail, per-product detail,
    and rendered HTML sample is appended to ``attempted`` on dispatch and
    to ``succeeded`` / ``skipped`` / ``failed`` on completion. Mid-snapshot
    abort (Phase 6 S-6/S-7 budget / quota exceeded) sets ``aborted_reason``
    in the snapshot's ``finally`` block before re-raising the exception,
    so the manifest reflects the partial state.

    ``partial`` is computed at write time by ``to_dict``: True iff any of
    ``skipped`` / ``failed`` is non-empty OR ``aborted_reason`` is not
    None OR ``attempted`` differs from ``succeeded``. Restore tooling
    (v1.5+) refuses to load a partial snapshot without ``--allow-partial``.
    """

    voog_mcp_version: str
    site: str
    host: str
    created_at: str  # ISO-8601 UTC
    attempted: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # [{endpoint, reason}]
    failed: list[dict] = field(default_factory=list)  # [{endpoint, reason}]
    request_count: int = 0
    duration_seconds: float = 0.0
    aborted_reason: str | None = None

    def to_dict(self) -> dict:
        # NOTE: `partial` is a *computed* field, not stored on the dataclass,
        # so callers can re-compute after every list mutation without state.
        partial = bool(
            self.skipped
            or self.failed
            or self.aborted_reason
            or set(self.attempted) != set(self.succeeded)
        )
        return {
            "voog_mcp_version": self.voog_mcp_version,
            "created_at": self.created_at,
            "site": self.site,
            "host": self.host,
            "attempted": list(self.attempted),
            "succeeded": list(self.succeeded),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "request_count": self.request_count,
            "duration_seconds": round(self.duration_seconds, 3),
            "aborted_reason": self.aborted_reason,
            "partial": partial,
        }


def _write_manifest(out: Path, manifest: _Manifest) -> None:
    """Write ``_meta.json`` to ``out``. Safe to call from ``finally`` — does
    NOT raise; on a filesystem error it best-effort-logs and returns. The
    snapshot's primary failure path should not be obscured by a manifest
    write error.
    """
    try:
        write_json(out / "_meta.json", manifest.to_dict())
    except Exception:  # pragma: no cover — defensive only
        pass


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
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "output_dir": {
                        "type": "string",
                        "description": "Absolute path where pages.json + page_{id}_contents.json files are written",
                    },
                },
                "required": ["site", "output_dir"],
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
                "VoogStyle capture. By default REFUSES to overwrite an existing "
                "directory — pick a fresh location. Pass overwrite=true to write "
                "into an existing directory (automation/cron use case); files "
                "from a prior snapshot may persist alongside new files if the "
                "underlying Voog state has shrunk. REQUIRED pre-flight before "
                "any risky operation: layout rename, mass push, layout swap, "
                "VoogStyle template push, page_delete. "
                "Writes _meta.json manifest to output_dir documenting "
                "voog-mcp version, attempted/succeeded/skipped/failed "
                "endpoints, request_count, duration_seconds, and (if the "
                "snapshot aborted mid-run) aborted_reason. Restore tooling "
                "reads this to refuse partial snapshots."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "output_dir": {
                        "type": "string",
                        "description": "Absolute path. Fresh (non-existing) by default; pass overwrite=true to allow existing.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": (
                            "Allow writing into an existing snapshot directory. "
                            "Default false; set true to overwrite a prior snapshot's output. "
                            "(Distinct from the force flag on delete tools, which authorizes "
                            "destruction. Here it only authorizes writing into an existing dir.)"
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "output_dir"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset({"pages_snapshot", "site_snapshot"})


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")

    # S9: tag every HTTP request inside this dispatch with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring. The
    # parallel_map fan-outs inside _site_snapshot / _pages_snapshot
    # propagate this tool context into worker threads via
    # voog._concurrency.propagate_tool_context (worker threads don't
    # inherit threading.local() across boundaries).
    with client.with_tool(name):
        if name == "pages_snapshot":
            return _pages_snapshot(arguments, client)
        if name == "site_snapshot":
            return _site_snapshot(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _snapshot_filename_for(endpoint: str) -> str:
    """`/redirect_rules` → `redirect_rules.json`."""
    return endpoint.lstrip("/").replace("/", "_") + ".json"


def _is_abort_exception(exc: BaseException) -> bool:
    """True if ``exc`` is a Phase 6 budget/quota signal that must bypass
    the per-endpoint forgiveness and abort the whole snapshot.

    Phase 5 lands before Phase 6, so the exception classes
    (``RequestBudgetExceeded`` / ``DailyQuotaExceeded``) don't exist yet
    — we key on class name for forward-compatibility. Phase 6's PR
    tightens this to ``isinstance(exc, (RequestBudgetExceeded, ...))``.

    Why this matters: both ``parallel_map`` workers AND the per-step
    ``try/except Exception`` would normally swallow these into
    ``manifest.failed``, so a budget hit would silently degrade to a
    partial snapshot instead of aborting. The whole point of the budget
    is to *stop work* once it's tripped.
    """
    return type(exc).__name__ in {"RequestBudgetExceeded", "DailyQuotaExceeded"}


def _classify_api_exc(exc: Exception) -> str:
    """Return ``"skipped"`` for 4xx (endpoint not available on this tenant /
    permission denied) and ``"failed"`` for 5xx / network errors / anything
    else. Restore tooling reads ``failed`` more strictly than ``skipped``.

    Voog API errors are httpx.HTTPStatusError post-Phase-1a; the public
    HTML fetch in step 6 still uses raw urllib so we accept both shapes.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return "skipped" if 400 <= code < 500 else "failed"
    if isinstance(exc, urllib.error.HTTPError) and exc.code is not None:
        return "skipped" if 400 <= exc.code < 500 else "failed"
    return "failed"


def _format_skip(label: str, exc: Exception) -> str:
    # Voog API errors now arrive as httpx.HTTPStatusError (post-S11); the
    # public-HTML rendered capture in step 6 still uses raw urllib, so we
    # keep the urllib branch as well.
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return f"{label}: endpoint not available (404)"
        return f"{label}: HTTP {code} {exc.response.reason_phrase}"
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return f"{label}: endpoint not available (404)"
    if isinstance(exc, urllib.error.HTTPError):
        return f"{label}: HTTP {exc.code} {exc.reason}"
    return f"{label}: {exc}"


def _pages_snapshot(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    output_dir = arguments.get("output_dir") or ""
    err = validate_output_dir(output_dir, tool_name="pages_snapshot", param_name="output_dir")
    if err:
        return error_response(err)

    out = Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
        pages = client.get_all("/pages")
        write_json(out / "pages.json", pages)
    except Exception as e:
        return error_response(f"pages_snapshot failed: {e}")

    page_ids = [p.get("id") for p in pages if p.get("id")]
    # S9d: propagate the parent's tool-tracking state into worker threads
    # so each /pages/{id}/contents fetch carries X-MCP-Tool +
    # X-Request-Id. Without this, parallel_map workers see empty
    # threading.local() state and emit untagged requests.
    page_content_results = parallel_map(
        propagate_tool_context(client, lambda pid: client.get(f"/pages/{pid}/contents")),
        page_ids,
        max_workers=8,
    )
    page_contents_written = 0
    per_page_errors: list = []
    for pid, contents, exc in page_content_results:
        if exc is not None:
            per_page_errors.append({"page_id": pid, "error": str(exc)})
            continue
        write_json(out / f"page_{pid}_contents.json", contents)
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
    # Defensive: catch v1.2.x callers whose MCP client doesn't enforce
    # ``additionalProperties: false``. Schema-level rejection alone isn't
    # enough — some MCP clients silently drop unknown args. Without this
    # check, ``force=true`` from a legacy caller would be ignored, the
    # missing ``overwrite`` would default to false, and the snapshot would
    # refuse to overwrite the dir — leaving the caller wondering why their
    # ``force=true`` did nothing. Loud explicit error wins.
    if "force" in arguments:
        return error_response(
            "site_snapshot: 'force' was renamed to 'overwrite' in v1.3. "
            "Pass overwrite=true instead. See CHANGELOG."
        )

    output_dir = arguments.get("output_dir") or ""
    err = validate_output_dir(output_dir, tool_name="site_snapshot", param_name="output_dir")
    if err:
        return error_response(err)

    out = Path(output_dir)
    overwrite = bool(arguments.get("overwrite"))
    if out.exists() and not overwrite:
        return error_response(
            f"site_snapshot: output_dir {output_dir!r} already exists. "
            "Pick a fresh location, or pass overwrite=true to write into it "
            "(automation/cron use case)."
        )

    try:
        out.mkdir(parents=True, exist_ok=overwrite)
    except Exception as e:
        return error_response(f"site_snapshot: cannot create {output_dir!r}: {e}")

    # Manifest scaffolding — populated as we go. ``arguments`` does not
    # carry the site name at this layer (strip_site removed it in
    # call_tool); we use client.host as the stable identifier and accept
    # that site name lives only in the dispatch-layer log. Future Phase 6
    # S-8 cross-site session hint will plumb site_name into the client.
    # ``isinstance(..., str)`` guards against MagicMock auto-attrs leaking
    # in from tests AND against any non-string client.site_name future.
    _site_name = getattr(client, "site_name", None)
    manifest = _Manifest(
        voog_mcp_version=_voog_version,
        site=_site_name if isinstance(_site_name, str) else "",
        host=client.host,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    started_at = time.monotonic()

    files_written = 0
    skipped: list = []
    pages_data: list = []
    articles_data: list = []
    products_data: list = []
    page_contents_count = 0
    article_detail_count = 0
    rendered_count = 0

    try:
        # 1. Standard list endpoints (paginated) — parallelized read-only fetches.
        # Items are ``(endpoint, params_or_None)`` tuples; the worker unpacks and
        # forwards params (None → bare list call). Keeps /layouts (which needs
        # ``include_body=true``) inside the parallel batch — no serial fallout.
        def _fetch_list(item: tuple[str, dict | None]):
            endpoint, params = item
            return client.get_all(endpoint, params=params)

        for endpoint, _params in SITE_SNAPSHOT_LIST_ENDPOINTS:
            manifest.attempted.append(endpoint)
        # S9d: wrap with propagate_tool_context so each worker thread
        # carries the with_tool scope's X-MCP-Tool + X-Request-Id headers.
        list_results = parallel_map(
            propagate_tool_context(client, _fetch_list),
            SITE_SNAPSHOT_LIST_ENDPOINTS,
            max_workers=8,
        )
        for (endpoint, _params), data, exc in list_results:
            filename = _snapshot_filename_for(endpoint)
            if exc is not None:
                # MD4: Phase 6 budget/quota exceptions bypass per-endpoint
                # forgiveness — re-raise so the snapshot aborts and the
                # finally clause writes the partial manifest.
                if _is_abort_exception(exc):
                    raise exc
                reason = _format_skip(filename, exc)
                # 4xx → skipped (endpoint not available on this tenant);
                # 5xx / network → failed (the fetch should have worked).
                bucket = _classify_api_exc(exc)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                skipped.append({"file": filename, "reason": reason})
                continue
            write_json(out / filename, data)
            files_written += 1
            manifest.succeeded.append(endpoint)
            if endpoint == "/pages":
                pages_data = data
            elif endpoint == "/articles":
                articles_data = data

        # 2. Singletons — kept sequential (only 2 endpoints, parallel speedup is
        # not worth the extra moving part).
        for endpoint in SITE_SNAPSHOT_SINGLETONS:
            manifest.attempted.append(endpoint)
            filename = _snapshot_filename_for(endpoint)
            try:
                data = client.get(endpoint)
            except Exception as e:
                if _is_abort_exception(e):
                    raise
                reason = _format_skip(filename, e)
                bucket = _classify_api_exc(e)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                skipped.append({"file": filename, "reason": reason})
                continue
            write_json(out / filename, data)
            files_written += 1
            manifest.succeeded.append(endpoint)

        # 3. Per-page contents — parallelized.
        page_ids = [p.get("id") for p in pages_data if p.get("id")]
        for pid in page_ids:
            manifest.attempted.append(f"/pages/{pid}/contents")
        # S9d: worker threads carry the parent's X-MCP-Tool + X-Request-Id.
        page_content_results = parallel_map(
            propagate_tool_context(client, lambda pid: client.get(f"/pages/{pid}/contents")),
            page_ids,
            max_workers=8,
        )
        for pid, contents, exc in page_content_results:
            endpoint = f"/pages/{pid}/contents"
            filename = f"page_{pid}_contents.json"
            if exc is not None:
                if _is_abort_exception(exc):
                    raise exc
                reason = _format_skip(filename, exc)
                bucket = _classify_api_exc(exc)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                skipped.append({"file": filename, "reason": reason})
                continue
            write_json(out / filename, contents)
            files_written += 1
            page_contents_count += 1
            manifest.succeeded.append(endpoint)

        # 4. Per-article details — parallelized.
        article_ids = [a.get("id") for a in articles_data if a.get("id")]
        for aid in article_ids:
            manifest.attempted.append(f"/articles/{aid}")
        # S9d: worker threads carry the parent's X-MCP-Tool + X-Request-Id.
        article_detail_results = parallel_map(
            propagate_tool_context(client, lambda aid: client.get(f"/articles/{aid}")),
            article_ids,
            max_workers=8,
        )
        for aid, detail, exc in article_detail_results:
            endpoint = f"/articles/{aid}"
            filename = f"article_{aid}.json"
            if exc is not None:
                if _is_abort_exception(exc):
                    raise exc
                reason = _format_skip(filename, exc)
                bucket = _classify_api_exc(exc)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                skipped.append({"file": filename, "reason": reason})
                continue
            write_json(out / filename, detail)
            files_written += 1
            article_detail_count += 1
            manifest.succeeded.append(endpoint)

        # 5. Ecommerce: products list with include=variants,variant_types,translations
        # — list response carries the full detail shape, so the per-product detail
        # fan-out (one GET per product) is eliminated. S2 in v1.4.
        #
        # Asymmetric vs S1 by design: no per-id fallback if Voog silently strips
        # the include from the list response. See PRODUCTS_DETAIL_INCLUDE in
        # voog.projections for the contract reasoning. A 4xx rejection of the
        # include lands in ``manifest.skipped[]``; downstream consumers continue
        # without products (same behaviour as any other list endpoint failure).
        manifest.attempted.append("/products")
        products_fetched = False
        try:
            products_data = client.get_all(
                "/products",
                base=client.ecommerce_url,
                params={"include": PRODUCTS_DETAIL_INCLUDE},
            )
            products_fetched = True
        except Exception as e:
            if _is_abort_exception(e):
                raise
            reason = _format_skip("products.json", e)
            bucket = _classify_api_exc(e)
            getattr(manifest, bucket).append({"endpoint": "/products", "reason": reason})
            skipped.append({"file": "products.json", "reason": reason})
            products_data = []

        if products_fetched:
            # /products itself succeeded — record even if the list is empty
            # (legitimate "no products on this site"). Without this, an
            # empty-but-valid products endpoint would make attempted differ
            # from succeeded and mark the snapshot partial.
            manifest.succeeded.append("/products")
        if products_data:
            write_json(out / "products.json", products_data)
            files_written += 1
            # S2: per-product files come from the list response directly — no
            # per-id GET fan-out. Each item already carries variants /
            # variant_types / translations via the list-level ?include.
            # Per-product files are derived from the same /products fetch,
            # so they are NOT separately tracked in attempted/succeeded —
            # the manifest models *HTTP requests made*, not *files written*.
            for product in products_data:
                pid = product.get("id")
                if not pid:
                    continue
                write_json(out / f"product_{pid}.json", product)
                files_written += 1

        # 6. Rendered HTML samples for VoogStyle capture (best-effort).
        # Public HTML fetch — no API key needed. Gracefully skipped if the host
        # is unreachable from the MCP server's network. All HTML fetch errors
        # land in ``manifest.skipped[]`` regardless of status: HTML samples are
        # explicitly best-effort and never block the API-level snapshot.
        sample_paths = _pick_sample_page_paths(pages_data)
        for path in sample_paths:
            endpoint = f"GET {path} (public HTML)"
            manifest.attempted.append(endpoint)
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
                reason = f"public fetch failed: {e}"
                manifest.skipped.append({"endpoint": endpoint, "reason": reason})
                skipped.append(
                    {
                        "file": f"voog_style_rendered_{slug}.html",
                        "reason": reason,
                    }
                )
                continue
            (out / f"voog_style_rendered_{slug}.html").write_text(html, encoding="utf-8")
            files_written += 1
            rendered_count += 1
            manifest.succeeded.append(endpoint)

    except Exception as exc:
        # MD4: forward-compatible catch for Phase 6's budget/quota
        # exceptions. Phase 6 introduces ``RequestBudgetExceeded`` and
        # ``DailyQuotaExceeded`` in ``voog.errors``; until that import
        # exists, key on class name. Phase 6's PR tightens this to
        # ``except (RequestBudgetExceeded, DailyQuotaExceeded) as exc:``.
        exc_class = type(exc).__name__
        if exc_class == "RequestBudgetExceeded":
            manifest.aborted_reason = "request_budget_exceeded"
        elif exc_class == "DailyQuotaExceeded":
            manifest.aborted_reason = "daily_quota_exceeded"
        else:
            # Any other unexpected exception still gets a recorded
            # aborted_reason so the manifest reflects "snapshot didn't
            # complete normally". The exception itself still re-raises.
            manifest.aborted_reason = f"unexpected:{exc_class}"
        # Re-raise — MD4 contract: manifest written in finally, exception
        # propagates so the caller sees the real failure.
        raise

    finally:
        # MD4: ``_meta.json`` MUST be written even on abort.
        # request_count: best-effort read from client. Phase 6 S-6 lands
        # the real counter; until then ``getattr`` falls back to 0.
        manifest.request_count = int(getattr(client, "_request_count", 0))
        manifest.duration_seconds = time.monotonic() - started_at
        _write_manifest(out, manifest)

    summary = (
        f"📦 site_snapshot: {files_written} files → {output_dir} "
        f"({len(pages_data)} pages, {len(articles_data)} articles, "
        f"{len(products_data)} products, {rendered_count} rendered HTML)"
    )
    if skipped:
        summary += f" ({len(skipped)} skipped/errored)"

    is_partial = manifest.to_dict()["partial"]
    if is_partial:
        summary += " [PARTIAL]"

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
            "partial": is_partial,
            "manifest_path": str(out / "_meta.json"),
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
