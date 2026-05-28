"""voog site-snapshot / pages-snapshot — read-only backup of Voog resources."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from voog import __version__ as _voog_version
from voog._concurrency import parallel_map
from voog.client import VoogClient
from voog.errors import DailyQuotaExceeded, RequestBudgetExceeded
from voog.projections import PRODUCTS_DETAIL_INCLUDE


def add_arguments(subparsers):
    snap_p = subparsers.add_parser(
        "site-snapshot",
        help="Comprehensive read-only backup of every Voog resource to <output_dir>",
    )
    snap_p.add_argument("output_dir", type=Path)
    snap_p.set_defaults(func=cmd_site_snapshot)

    pages_p = subparsers.add_parser(
        "pages-snapshot",
        help="Backup all pages + per-page contents to JSON files",
    )
    pages_p.add_argument("output_dir", type=Path)
    pages_p.set_defaults(func=cmd_pages_snapshot)


def cmd_site_snapshot(args, client: VoogClient) -> int:
    """Comprehensive backup — all list endpoints, singletons, per-page/article/product details.

    Writes a ``_meta.json`` manifest to ``output_dir`` documenting the
    snapshot's coverage (attempted / succeeded / skipped / failed
    endpoints, version, duration, request count, abort reason if any).
    Same fields, same partial-detection, same abort-handling as the
    MCP ``site_snapshot`` tool (Phase 5 S7 + MD4).
    """
    import urllib.request

    from voog.mcp.tools.snapshot import (
        SITE_SNAPSHOT_LIST_ENDPOINTS,
        SITE_SNAPSHOT_SINGLETONS,
        _classify_api_exc,
        _format_skip,
        _is_abort_exception,
        _Manifest,
        _pick_sample_page_paths,
        _slugify_path,
        _snapshot_filename_for,
        _write_manifest,
    )

    out = Path(args.output_dir)
    if out.exists():
        sys.stderr.write(
            f"error: output directory already exists: {out}\n"
            "  Choose a different directory or remove the old snapshot first.\n"
        )
        return 1

    try:
        out.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        sys.stderr.write(f"error: cannot create {out}: {e}\n")
        return 1

    print(f"Site-snapshot: {client.host} -> {out}/")

    # Manifest scaffolding — same shape as the MCP path so downstream
    # tooling (restore in v1.5+, partial-snapshot detection) sees one
    # contract regardless of which surface wrote the snapshot.
    # ``isinstance(..., str)`` guards against MagicMock auto-attrs from
    # tests leaking a non-string site_name into the manifest.
    _site_name = getattr(client, "site_name", None)
    manifest = _Manifest(
        voog_mcp_version=_voog_version,
        site=_site_name if isinstance(_site_name, str) else "",
        host=client.host,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    started_at = time.monotonic()

    written = 0
    pages_data = []
    articles_data = []
    products_data = []
    aborted_exception: BaseException | None = None

    try:
        # 1. Standard list endpoints. SITE_SNAPSHOT_LIST_ENDPOINTS is
        # ``[(endpoint, params_or_None), ...]`` — params carries any
        # required query-string modifier (e.g. ``include_body=true``
        # for /layouts) so CLI and MCP can't drift on per-endpoint
        # shapes (v1.4 design fix).
        for endpoint, params in SITE_SNAPSHOT_LIST_ENDPOINTS:
            manifest.attempted.append(endpoint)
            filename = _snapshot_filename_for(endpoint)
            try:
                data = client.get_all(endpoint, params=params)
            except Exception as e:
                if _is_abort_exception(e):
                    raise
                reason = _format_skip(filename, e)
                bucket = _classify_api_exc(e)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                print(f"  skipped {filename}: {e}")
                continue
            _write_json(out / filename, data)
            print(f"  {filename} ({len(data)})")
            written += 1
            manifest.succeeded.append(endpoint)
            if endpoint == "/pages":
                pages_data = data
            elif endpoint == "/articles":
                articles_data = data

        # 2. Singletons
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
                print(f"  skipped {filename}: {e}")
                continue
            _write_json(out / filename, data)
            print(f"  {filename}")
            written += 1
            manifest.succeeded.append(endpoint)

        # 3. Per-page contents — parallelized (mirror of MCP _site_snapshot).
        page_ids = [p.get("id") for p in pages_data if p.get("id")]
        for pid in page_ids:
            manifest.attempted.append(f"/pages/{pid}/contents")
        page_content_results = parallel_map(
            lambda pid: client.get(f"/pages/{pid}/contents"),
            page_ids,
            max_workers=8,
        )
        page_contents_count = 0
        for pid, contents, exc in page_content_results:
            endpoint = f"/pages/{pid}/contents"
            if exc is not None:
                if _is_abort_exception(exc):
                    raise exc
                reason = _format_skip(f"page_{pid}_contents.json", exc)
                bucket = _classify_api_exc(exc)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                print(f"  warning: page {pid} contents: {exc}")
                continue
            _write_json(out / f"page_{pid}_contents.json", contents)
            written += 1
            page_contents_count += 1
            manifest.succeeded.append(endpoint)
        if page_contents_count:
            print(f"  page contents x {page_contents_count}")

        # 4. Per-article details — parallelized.
        article_ids = [a.get("id") for a in articles_data if a.get("id")]
        for aid in article_ids:
            manifest.attempted.append(f"/articles/{aid}")
        article_detail_results = parallel_map(
            lambda aid: client.get(f"/articles/{aid}"),
            article_ids,
            max_workers=8,
        )
        article_detail_count = 0
        for aid, detail, exc in article_detail_results:
            endpoint = f"/articles/{aid}"
            if exc is not None:
                if _is_abort_exception(exc):
                    raise exc
                reason = _format_skip(f"article_{aid}.json", exc)
                bucket = _classify_api_exc(exc)
                getattr(manifest, bucket).append({"endpoint": endpoint, "reason": reason})
                print(f"  warning: article {aid}: {exc}")
                continue
            _write_json(out / f"article_{aid}.json", detail)
            written += 1
            article_detail_count += 1
            manifest.succeeded.append(endpoint)
        if article_detail_count:
            print(f"  article details x {article_detail_count}")

        # 5. Ecommerce: products list with include=variants,variant_types,translations
        # — list response carries the full detail shape, so the per-product detail
        # fan-out (one GET per product) is eliminated. Mirrors MCP tool S2 (v1.4).
        # See snapshot.py:_site_snapshot for the docs-citation + design rationale
        # explaining why there's no per-id fallback (asymmetric vs S1).
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
            print(f"  skipped products.json: {e}")
            products_data = []

        if products_fetched:
            # /products itself succeeded — record even if list is empty
            # (legitimate "no products on this site"). Without this,
            # an empty-but-valid products endpoint would make attempted
            # differ from succeeded and mark the snapshot partial.
            manifest.succeeded.append("/products")
        if products_data:
            _write_json(out / "products.json", products_data)
            print(f"  products.json ({len(products_data)})")
            written += 1
            # S2: per-product files come from the list response directly — no
            # per-id GET fan-out. Each item already carries variants /
            # variant_types / translations via the list-level ?include.
            # Per-product files are derived from the same /products fetch,
            # so they are NOT separately tracked in attempted/succeeded —
            # the manifest models *HTTP requests made*, not *files written*.
            product_detail_count = 0
            for product in products_data:
                pid = product.get("id")
                if not pid:
                    continue
                _write_json(out / f"product_{pid}.json", product)
                written += 1
                product_detail_count += 1
            if product_detail_count:
                print(f"  product details x {product_detail_count}")

        # 6. Rendered HTML samples for VoogStyle capture (best-effort).
        # Public HTML fetch — no API key needed. Gracefully skipped if the host
        # is unreachable. All HTML fetch errors land in ``manifest.skipped[]``
        # regardless of status: HTML samples are explicitly best-effort and
        # never block the API-level snapshot.
        rendered_count = 0
        for path in _pick_sample_page_paths(pages_data):
            endpoint = f"GET {path} (public HTML)"
            manifest.attempted.append(endpoint)
            slug = _slugify_path(path)
            url = f"https://{client.host}{path}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 voog-mcp-snapshot/1.0"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                reason = f"public fetch failed: {e}"
                manifest.skipped.append({"endpoint": endpoint, "reason": reason})
                print(f"  warning: {url}: {e}")
                continue
            (out / f"voog_style_rendered_{slug}.html").write_text(html, encoding="utf-8")
            print(f"  voog_style_rendered_{slug}.html")
            written += 1
            rendered_count += 1
            manifest.succeeded.append(endpoint)

    except RequestBudgetExceeded as exc:
        # MD4: snapshot aborted by the per-VoogClient request budget.
        # Known operational abort — record + return non-zero so CLI
        # callers (cron, CI) detect it without a Python traceback.
        manifest.aborted_reason = "request_budget_exceeded"
        aborted_exception = exc
    except DailyQuotaExceeded as exc:
        # Known operational abort by per-site daily quota.
        manifest.aborted_reason = "daily_quota_exceeded"
        aborted_exception = exc
    except BaseException as exc:
        # Anything else (programming bugs, KeyboardInterrupt, unexpected
        # network errors that aren't already caught per-endpoint): record
        # the abort reason in the manifest so `_meta.json` reflects the
        # partial state, then RE-RAISE so the operator gets the real
        # traceback. Matches MCP path behaviour (which also re-raises
        # unexpected exceptions after recording aborted_reason). Without
        # the re-raise we'd silently swallow real bugs as "clean rc=1
        # aborts", obscuring diagnostics.
        manifest.aborted_reason = f"unexpected:{type(exc).__name__}"
        raise

    finally:
        # MD4: ``_meta.json`` MUST be written even on abort. Same
        # request_count plumbing as MCP path — Phase 6 S-6 always
        # sets ``_request_count = 0`` on real ``VoogClient`` instances;
        # ``hasattr`` keeps the test fixture that ``del client._request_count``
        # working without surfacing a MagicMock auto-attr as the value.
        if hasattr(client, "_request_count"):
            try:
                manifest.request_count = int(client._request_count)
            except (TypeError, ValueError):
                pass
        manifest.duration_seconds = time.monotonic() - started_at
        _write_manifest(out, manifest)

    if aborted_exception is not None:
        sys.stderr.write(
            f"error: snapshot aborted ({manifest.aborted_reason}): {aborted_exception}\n"
        )
        return 1

    print(f"\nSnapshot complete: {written} resources backed up to {out}/")
    return 0


def cmd_pages_snapshot(args, client: VoogClient) -> int:
    """Backup all pages + per-page contents to JSON files."""
    out = Path(args.output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        sys.stderr.write(f"error: cannot create {out}: {e}\n")
        return 1

    pages = client.get_all("/pages")
    _write_json(out / "pages.json", pages)
    print(f"  pages.json: {len(pages)} pages")

    page_ids = [p.get("id") for p in pages if p.get("id")]
    results = parallel_map(
        lambda pid: client.get(f"/pages/{pid}/contents"),
        page_ids,
        max_workers=8,
    )
    errors = 0
    for pid, contents, exc in results:
        if exc is not None:
            print(f"  warning: page {pid} contents failed: {exc}")
            errors += 1
            continue
        _write_json(out / f"page_{pid}_contents.json", contents)

    print(f"  Snapshot complete: {out}")
    if errors:
        print(f"  {errors} page(s) had errors")
    return 0 if not errors else 1


def _write_json(path: Path, data) -> None:
    import json

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
