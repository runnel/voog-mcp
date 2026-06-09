"""voog pull — download all template files from a Voog site."""

from __future__ import annotations

import json
from pathlib import Path

from voog.client import VoogClient


def add_arguments(subparsers):
    p = subparsers.add_parser("pull", help="Download all template files into cwd")
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    local_dir = Path.cwd()
    print(f"Connecting to: {client.host}")
    local_dir.mkdir(exist_ok=True)

    # 1. Layouts (templates)
    print("\nFetching layouts...")
    layouts = client.get_all("/layouts")
    layouts_dir = local_dir / "layouts"
    layouts_dir.mkdir(exist_ok=True)

    manifest = {}
    asset_type_to_folder = {
        "stylesheet": "stylesheets",
        "javascript": "javascripts",
        "image": "images",
        "font": "assets",
        "unknown": "assets",
    }

    for layout in layouts:
        folder = "components" if layout.get("component") else "layouts"
        folder_path = local_dir / folder
        folder_path.mkdir(exist_ok=True)
        filename = f"{layout['title']}.tpl"
        filepath = folder_path / filename
        detail = client.get(f"/layouts/{layout['id']}")
        body = detail.get("body", "") or ""
        filepath.write_text(body, encoding="utf-8")
        manifest[str(filepath.relative_to(local_dir))] = {
            "id": layout["id"],
            "type": "layout",
            "updated_at": layout.get("updated_at", ""),
        }
        print(f"  ✓ {folder}/{filename}")

    # 2. Layout assets (CSS, JS, images, fonts)
    print("\nFetching layout_assets...")
    assets = client.get_all("/layout_assets")
    for asset in assets:
        # The list endpoint describes the asset type as `asset_type`
        # (older API responses used `kind` — accept both).
        kind = asset.get("asset_type") or asset.get("kind") or "unknown"
        folder_name = asset_type_to_folder.get(kind, "assets")
        folder_path = local_dir / folder_name
        folder_path.mkdir(exist_ok=True)
        filename = asset["filename"]
        filepath = folder_path / filename
        # Text assets carry a `data` field; binary assets only a public_url.
        # The list endpoint stopped including `data` (observed 2026-06) —
        # only /layout_assets/{id} returns it now, so editable (= text)
        # assets need a per-asset detail GET, mirroring the layouts loop.
        data = asset.get("data")
        if data is None and asset.get("editable"):
            detail = client.get(f"/layout_assets/{asset['id']}")
            data = detail.get("data")
        if data is None:
            # Skip binaries during pull (they don't round-trip well via API)
            continue
        filepath.write_text(data, encoding="utf-8")
        manifest[str(filepath.relative_to(local_dir))] = {
            "id": asset["id"],
            "type": "asset",
            "kind": kind,
            "updated_at": asset.get("updated_at", ""),
        }
        print(f"  ✓ {folder_name}/{filename}")

    # 3. Site data (settings)
    print("\nFetching site data...")
    site = client.get("/site")
    sitedata_path = local_dir / "site-data.json"
    sitedata_path.write_text(json.dumps(site, indent=2), encoding="utf-8")

    manifest_path = local_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n✅ All files saved to: {local_dir}")
    return 0
