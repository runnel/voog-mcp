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

    failed = 0
    for rel_path in targets:
        entry = manifest[rel_path]
        body = (local_dir / rel_path).read_text(encoding="utf-8")
        kind = entry["type"]
        # Both endpoints take a flat payload — wrapping {"layout_asset": …}
        # is silently 200-ed without persisting (issue #96).
        if kind == "layout":
            path, content_field = f"/layouts/{entry['id']}", "body"
        elif kind == "asset":
            path, content_field = f"/layout_assets/{entry['id']}", "data"
        else:
            sys.stderr.write(f"  ✗ {rel_path}: unknown manifest type {kind!r}\n")
            failed += 1
            continue
        result = client.put(path, {content_field: body})
        # Silent-no-op detector for issue #96's symptom: 200 with the
        # resource echoed back but the content field cleared. Narrow on
        # purpose — slim responses that omit the field stay accepted.
        if (
            body
            and isinstance(result, dict)
            and content_field in result
            and not result[content_field]
        ):
            sys.stderr.write(
                f"  ✗ {rel_path}: PUT returned 200 but stored "
                f"{content_field!r} is empty — content NOT updated on Voog\n"
            )
            failed += 1
            continue
        print(f"  ✓ {rel_path}")
    return 2 if failed else 0
