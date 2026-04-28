"""voog push — upload modified template files to Voog."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient


def add_arguments(subparsers):
    p = subparsers.add_parser(
        "push", help="Upload file(s) to Voog. No args = push all (with confirmation)."
    )
    p.add_argument("files", nargs="*", help="Specific files to push")
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    local_dir = Path.cwd()
    manifest_path = local_dir / "manifest.json"
    if not manifest_path.exists():
        sys.stderr.write("error: manifest.json missing. Run `voog pull` first.\n")
        return 1

    manifest = json.loads(manifest_path.read_text())

    if args.files:
        targets = []
        for f in args.files:
            rel = str(Path(f).relative_to(local_dir)) if Path(f).is_absolute() else f
            if rel not in manifest:
                sys.stderr.write(f"error: {rel} not in manifest. Skipping.\n")
                continue
            targets.append(rel)
    else:
        confirm = input(f"Push ALL {len(manifest)} tracked files? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return 0
        targets = list(manifest)

    for rel_path in targets:
        entry = manifest[rel_path]
        body = (local_dir / rel_path).read_text(encoding="utf-8")
        if entry["type"] == "layout":
            client.put(f"/layouts/{entry['id']}", {"layout": {"body": body}})
        elif entry["type"] == "asset":
            client.put(f"/layout_assets/{entry['id']}", {"layout_asset": {"data": body}})
        print(f"  ✓ {rel_path}")
    return 0
