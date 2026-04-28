"""voog site-snapshot / pages-snapshot — read-only backup of Voog resources."""
from __future__ import annotations

import sys
from pathlib import Path

from voog.client import VoogClient


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
    """Comprehensive backup — all list endpoints, singletons, per-page/article/product details."""
    from voog.mcp.tools.snapshot import (
        _snapshot_filename_for,
        _pick_sample_page_paths,
        _slugify_path,
        SITE_SNAPSHOT_LIST_ENDPOINTS,
        SITE_SNAPSHOT_SINGLETONS,
    )
    import json
    import urllib.request

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
    written = 0
    pages_data = []
    articles_data = []
    products_data = []

    # 1. Standard list endpoints
    for endpoint in SITE_SNAPSHOT_LIST_ENDPOINTS:
        filename = _snapshot_filename_for(endpoint)
        try:
            data = client.get_all(endpoint)
        except Exception as e:
            print(f"  skipped {filename}: {e}")
            continue
        _write_json(out / filename, data)
        print(f"  {filename} ({len(data)})")
        written += 1
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
            print(f"  skipped {filename}: {e}")
            continue
        _write_json(out / filename, data)
        print(f"  {filename}")
        written += 1

    # 3. Per-page contents
    page_contents_count = 0
    for p in pages_data:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = client.get(f"/pages/{pid}/contents")
        except Exception as e:
            print(f"  warning: page {pid} contents: {e}")
            continue
        _write_json(out / f"page_{pid}_contents.json", contents)
        written += 1
        page_contents_count += 1
    if page_contents_count:
        print(f"  page contents x {page_contents_count}")

    # 4. Per-article details
    article_detail_count = 0
    for a in articles_data:
        aid = a.get("id")
        if not aid:
            continue
        try:
            detail = client.get(f"/articles/{aid}")
        except Exception as e:
            print(f"  warning: article {aid}: {e}")
            continue
        _write_json(out / f"article_{aid}.json", detail)
        written += 1
        article_detail_count += 1
    if article_detail_count:
        print(f"  article details x {article_detail_count}")

    # 5. Ecommerce: products list + per-product details
    try:
        products_data = client.get_all("/products", base=client.ecommerce_url)
    except Exception as e:
        print(f"  skipped products.json: {e}")
        products_data = []

    if products_data:
        _write_json(out / "products.json", products_data)
        print(f"  products.json ({len(products_data)})")
        written += 1
        product_detail_count = 0
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
                print(f"  warning: product {pid}: {e}")
                continue
            _write_json(out / f"product_{pid}.json", detail)
            written += 1
            product_detail_count += 1
        if product_detail_count:
            print(f"  product details x {product_detail_count}")

    # 6. Rendered HTML samples for VoogStyle capture
    rendered_count = 0
    for path in _pick_sample_page_paths(pages_data):
        slug = _slugify_path(path)
        url = f"https://{client.host}{path}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 voog-mcp-snapshot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  warning: {url}: {e}")
            continue
        (out / f"voog_style_rendered_{slug}.html").write_text(html, encoding="utf-8")
        print(f"  voog_style_rendered_{slug}.html")
        written += 1
        rendered_count += 1

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

    errors = 0
    for p in pages:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = client.get(f"/pages/{pid}/contents")
        except Exception as e:
            print(f"  warning: page {pid} contents failed: {e}")
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
