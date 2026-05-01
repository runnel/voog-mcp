"""voog layouts — rename layouts, replace assets, create layouts/components."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient


def add_arguments(subparsers):
    rename_p = subparsers.add_parser(
        "layout-rename",
        help="Rename a layout (API + manifest + file on disk)",
    )
    rename_p.add_argument("layout_id", type=int)
    rename_p.add_argument("new_title")
    rename_p.set_defaults(func=cmd_layout_rename)

    replace_p = subparsers.add_parser(
        "asset-replace",
        help="Replace a layout_asset filename (DELETE+POST workaround)",
    )
    replace_p.add_argument("asset_id", type=int)
    replace_p.add_argument("new_filename")
    replace_p.set_defaults(func=cmd_asset_replace)

    create_p = subparsers.add_parser(
        "layout-create",
        help="Create a new layout or component in Voog (POST /layouts)",
    )
    create_p.add_argument(
        "args",
        nargs="+",
        metavar="[kind] path",
        help="[layout|component] path/to/file.tpl [--content-type=page]",
    )
    create_p.set_defaults(func=cmd_layout_create)


def cmd_layout_rename(args, client: VoogClient) -> int:
    layout_id = args.layout_id
    new_title = args.new_title

    if "/" in new_title or "\\" in new_title or new_title.startswith("."):
        sys.stderr.write(
            f"error: layout title must not contain '/' or '\\' or start with '.': {new_title!r}\n"
        )
        return 2

    # 1. API call
    print(f"PUT /layouts/{layout_id} title={new_title!r}...")
    client.put(f"/layouts/{layout_id}", {"title": new_title})

    # 2. Find old path in manifest
    local_dir = Path.cwd()
    manifest_path = local_dir / "manifest.json"
    if not manifest_path.exists():
        print("  warning: manifest.json missing — file and manifest update skipped.")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    old_path = None
    folder = None
    for path, info in manifest.items():
        if info.get("id") == layout_id and info.get("type") == "layout":
            old_path = path
            folder = path.split("/", 1)[0]  # "layouts" or "components"
            break

    if old_path is None:
        print(f"  warning: layout id {layout_id} not found in manifest — only API updated.")
        return 0

    new_path = f"{folder}/{new_title}.tpl"

    # 3. Rename file on disk
    old_file = local_dir / old_path
    new_file = local_dir / new_path
    if old_file.exists():
        new_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.rename(new_file)
        print(f"  {old_path} -> {new_path}")

    # 4. Update manifest
    info = manifest.pop(old_path)
    manifest[new_path] = info
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("  manifest.json updated")
    return 0


def cmd_asset_replace(args, client: VoogClient) -> int:
    """Replace a layout_asset by DELETE+POST (Voog PUT /layout_assets filename bug workaround)."""
    asset_id = args.asset_id
    new_filename = args.new_filename

    if "/" in new_filename or "\\" in new_filename or new_filename.startswith("."):
        sys.stderr.write(
            f"error: asset filename must not contain '/' or '\\' or start with '.': {new_filename!r}\n"
        )
        return 2

    local_dir = Path.cwd()

    # 1. GET old asset metadata + content
    print(f"GET /layout_assets/{asset_id}...")
    old_asset = client.get(f"/layout_assets/{asset_id}")
    asset_type = old_asset.get("asset_type")
    old_filename = old_asset.get("filename")
    content = old_asset.get("data")

    # 2. Find manifest entry
    manifest_path = local_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    old_path = None
    folder = None
    for path, info in manifest.items():
        # ``pull.py`` writes layout-asset entries with ``type="asset"``;
        # the legacy ``voog.py`` tooling wrote ``"layout_asset"``. Match
        # both, otherwise the rename-on-disk + manifest-update branch
        # silently no-ops on long-lived checkouts (root cause of #96 in
        # `voog push`; identical bug pattern lurked here).
        if info.get("id") == asset_id and info.get("type") in ("asset", "layout_asset"):
            old_path = path
            folder = path.split("/", 1)[0]
            break

    # Fallback: read content from local file if API didn't return it
    if content is None and old_path is not None:
        local_file = local_dir / old_path
        if local_file.exists():
            content = local_file.read_text(encoding="utf-8")

    if content is None:
        sys.stderr.write(
            f"error: cannot read content of asset {asset_id} (neither API nor local file)\n"
        )
        return 1

    # 3. POST new asset
    print(f"POST /layout_assets filename={new_filename!r}...")
    result = client.post(
        "/layout_assets",
        {
            "filename": new_filename,
            "asset_type": asset_type,
            "data": content,
        },
    )
    new_id = result.get("id")
    if not new_id:
        sys.stderr.write(f"error: POST response missing id: {result!r}\n")
        return 1

    # 4. Update manifest + local file
    if old_path and folder:
        new_path = f"{folder}/{new_filename}"
        old_file = local_dir / old_path
        new_file = local_dir / new_path
        if old_file.exists():
            new_file.parent.mkdir(parents=True, exist_ok=True)
            old_file.rename(new_file)
        manifest.pop(old_path, None)
        manifest[new_path] = {
            "id": new_id,
            # Match pull.py's manifest schema exactly. ``pull.py`` writes
            # ``{"type": "asset", "kind": kind, ...}``; using "kind" here
            # keeps replaced entries readable by any future code that
            # filters/groups by manifest["…"]["kind"].
            "type": "asset",
            "kind": asset_type,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  Asset replaced: {old_path} (id:{asset_id}) -> {new_path} (id:{new_id})")
    else:
        print(f"  POST OK: new id {new_id} (manifest not updated — old entry not found)")

    # 5. Warn about leftover old asset
    print(f"  warning: old asset id {asset_id} ({old_filename!r}) is still present in Voog.")
    print("  After updating + pushing templates that reference the old name, delete with:")
    print(f"  curl -X DELETE 'https://{client.host}/admin/api/layout_assets/{asset_id}' \\")
    print('       -H "X-API-Token: $VOOG_API_KEY"')
    return 0


def cmd_layout_create(args, client: VoogClient) -> int:
    """Create a new layout or component (POST /layouts).

    Supports:
      voog layout-create path/to/file.tpl
      voog layout-create layout path/to/file.tpl
      voog layout-create component path/to/file.tpl
    Either form accepts --content-type=<value> anywhere in args.
    """
    local_dir = Path.cwd()

    raw = args.args
    content_type = None
    positional = []
    for a in raw:
        if a.startswith("--content-type="):
            content_type = a.split("=", 1)[1] or None
        else:
            positional.append(a)

    if len(positional) == 1:
        kind = None
        file_path = positional[0]
    elif len(positional) == 2:
        kind = positional[0]
        file_path = positional[1]
    else:
        sys.stderr.write(
            "Usage: voog layout-create [--content-type=<ct>] path\n"
            "       voog layout-create [--content-type=<ct>] kind path\n"
            "  kind: layout | component (optional, derived from path)\n"
            "  content_type: page (default) | blog_article | blog | ...\n"
        )
        return 2

    # Resolve path robustly (absolute, ./, ../ all handled)
    try:
        full_path = (local_dir / file_path).resolve()
        rel_path = str(full_path.relative_to(local_dir.resolve()))
    except ValueError:
        sys.stderr.write(f"error: path must be inside the repo: {file_path}\n")
        return 2

    if not full_path.exists():
        sys.stderr.write(f"error: file not found: {full_path}\n")
        return 1

    # Derive kind from parent folder
    parent = full_path.parent.name
    if parent == "components":
        derived_kind = "component"
    elif parent == "layouts":
        derived_kind = "layout"
    else:
        sys.stderr.write(
            f"error: path must be under 'components/' or 'layouts/', got: {rel_path}\n"
        )
        return 2

    if kind is not None and kind != derived_kind:
        sys.stderr.write(
            f"error: kind={kind!r} does not match path {rel_path} (expected {derived_kind!r})\n"
        )
        return 2

    kind = derived_kind

    # Collision check
    manifest_path = local_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    if rel_path in manifest:
        existing_id = manifest[rel_path].get("id")
        sys.stderr.write(
            f"error: {rel_path} already exists in manifest (id:{existing_id})\n"
            f"  Use 'voog push {rel_path}' to update an existing layout.\n"
            f"  Or remove it from the manifest and Voog before recreating.\n"
        )
        return 1

    body = full_path.read_text(encoding="utf-8")
    title = full_path.stem

    payload = {
        "title": title,
        "body": body,
        "component": (kind == "component"),
    }
    if kind == "layout":
        payload["content_type"] = content_type or "page"

    print(f"POST /layouts title={title!r} component={kind == 'component'}...")
    result = client.post("/layouts", payload)
    new_id = result.get("id")
    if not new_id:
        sys.stderr.write(f"error: POST response missing id: {result!r}\n")
        return 1

    manifest[rel_path] = {
        "id": new_id,
        "type": "layout",
        "updated_at": result.get("updated_at", ""),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  Created {kind}: {rel_path} (id:{new_id})")
    print("  manifest.json updated.")
    return 0
