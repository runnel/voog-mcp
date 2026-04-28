"""voog products — list and manage products."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from voog.client import VoogClient


CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def add_arguments(subparsers):
    list_p = subparsers.add_parser("products", help="List all products")
    list_p.set_defaults(func=cmd_list)

    info_p = subparsers.add_parser("product", help="Get or update a product")
    info_p.add_argument("product_id", type=int)
    info_p.add_argument(
        "fields", nargs="*",
        help="Field/value pairs: name-et 'X' name-en 'Y' slug-et 'x' slug-en 'y'",
    )
    info_p.set_defaults(func=cmd_product)

    img_p = subparsers.add_parser(
        "product-image", help="Replace a product's images (first = main image)"
    )
    img_p.add_argument("product_id", type=int)
    img_p.add_argument("files", nargs="+", type=Path)
    img_p.set_defaults(func=cmd_product_image)


def cmd_list(args, client: VoogClient) -> int:
    products = client.get_all(
        "/products",
        base=client.ecommerce_url,
        params={"include": "translations"},
    )
    print(f"{'ID':<12} {'Slug':<40} Name")
    print("-" * 80)
    for p in products:
        pid = str(p.get("id", ""))
        slug = p.get("slug", "") or ""
        name = p.get("name", "") or ""
        name_clean = name.replace("﻿", "").replace("​", "")
        print(f"{pid:<12} {slug:<40} {name_clean}")
    print(f"\nTotal: {len(products)} products")
    return 0


def cmd_product(args, client: VoogClient) -> int:
    pid = args.product_id
    fields = args.fields

    if not fields:
        prod = client.get(
            f"/products/{pid}",
            base=client.ecommerce_url,
            params={"include": "variant_types,variants,translations"},
        )
        print(json.dumps(prod, indent=2, ensure_ascii=False))
        return 0

    if len(fields) % 2 != 0:
        sys.stderr.write("error: fields must come in key/value pairs\n")
        return 2

    translations: dict[str, dict[str, str]] = {"name": {}, "slug": {}}
    for i in range(0, len(fields), 2):
        key, value = fields[i], fields[i + 1]
        if "-" not in key:
            sys.stderr.write(
                f"error: unknown field {key!r}. Use 'name-et', 'name-en', 'slug-et', etc.\n"
            )
            return 2
        attr, lang = key.split("-", 1)
        if attr not in ("name", "slug"):
            sys.stderr.write(
                f"error: unknown field {attr!r}. Allowed: name, slug\n"
            )
            return 2
        translations[attr][lang] = value

    # Remove empty dicts
    translations = {k: v for k, v in translations.items() if v}

    payload = {"product": {"translations": translations}}
    result = client.put(f"/products/{pid}", payload, base=client.ecommerce_url)

    print(f"  Updated:")
    print(f"  name: {result.get('name', '')!r}")
    print(f"  slug: {result.get('slug', '')}")

    # Show updated translations
    updated = client.get(
        f"/products/{pid}",
        base=client.ecommerce_url,
        params={"include": "translations"},
    )
    tr = updated.get("translations") or {}
    for field in ("name", "slug"):
        if field in tr:
            vals = tr[field]
            print(
                f"  {field}: "
                + ", ".join(f"{l}={v!r}" for l, v in (vals or {}).items())
            )
    return 0


def cmd_product_image(args, client: VoogClient) -> int:
    """Replace product images via the 3-step Voog asset upload protocol.

    Steps per file:
      1. POST /assets → {id, upload_url}
      2. PUT upload_url with raw binary (S3, not JSON, not Voog-auth'd)
      3. PUT /assets/{id}/confirm → asset becomes usable

    Then PUT /products/{id} {image_id, asset_ids} to link images.
    """
    product_id = args.product_id
    files = args.files

    # Validate all files upfront
    paths: list[Path] = []
    for f in files:
        p = Path(f)
        if not p.exists():
            sys.stderr.write(f"error: file not found: {f}\n")
            return 1
        ext = p.suffix.lower()
        if ext not in CONTENT_TYPES:
            sys.stderr.write(
                f"error: unsupported file type {ext!r}. "
                f"Allowed: {', '.join(sorted(CONTENT_TYPES))}\n"
            )
            return 2
        paths.append(p)

    # Preflight: fetch current product
    try:
        prod = client.get(f"/products/{product_id}", base=client.ecommerce_url)
    except Exception as e:
        sys.stderr.write(f"error: cannot fetch product {product_id}: {e}\n")
        return 1

    print(f"Product: {prod.get('name', '?')} (id:{product_id})")
    old_ids = prod.get("asset_ids", [])
    if old_ids:
        print(f"  Existing images: {old_ids}")

    # Upload files
    asset_ids = []
    for p in paths:
        print(f"  Uploading {p.name}...", end="", flush=True)
        try:
            asset = _upload_asset(p, client)
        except Exception as e:
            sys.stderr.write(f"\nerror: upload failed for {p.name}: {e}\n")
            return 1
        asset_ids.append(asset["id"])
        dims = (
            f"{asset['width']}x{asset['height']}" if asset.get("width") else "processing"
        )
        print(f" done (id:{asset['id']}, {dims})")

    # Update product
    result = client.put(
        f"/products/{product_id}",
        {"image_id": asset_ids[0], "asset_ids": asset_ids},
        base=client.ecommerce_url,
    )

    img = result.get("image") or {}
    dims = (
        f"{img['width']}x{img['height']}" if img.get("width") else "processing"
    )
    print(f"\n  Images updated!")
    print(f"  Main image: id:{asset_ids[0]} ({dims})")
    print(f"  Total images: {len(asset_ids)}")
    print(f"  Asset IDs: {result.get('asset_ids', [])}")
    return 0


def _upload_asset(path: Path, client: VoogClient) -> dict:
    """3-step Voog asset upload. Returns {id, url, width, height}."""
    content_type = CONTENT_TYPES[path.suffix.lower()]
    size = path.stat().st_size

    # 1. Create asset record
    asset = client.post("/assets", {
        "filename": path.name,
        "content_type": content_type,
        "size": size,
    })
    asset_id = asset["id"]
    upload_url = asset["upload_url"]

    # 2. Raw binary PUT to S3-style URL (NOT through VoogClient — no JSON, no auth)
    file_data = path.read_bytes()
    req = urllib.request.Request(
        upload_url,
        data=file_data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "x-amz-acl": "public-read",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"S3 upload failed: HTTP {resp.status}")

    # 3. Confirm asset
    confirmed = client.put(f"/assets/{asset_id}/confirm")

    return {
        "id": asset_id,
        "url": confirmed.get("public_url", "") if confirmed else "",
        "width": confirmed.get("width") if confirmed else None,
        "height": confirmed.get("height") if confirmed else None,
    }
