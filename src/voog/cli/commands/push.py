"""voog push — upload modified template files to Voog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient

# Voog uses **flat** payloads for both /layouts and /layout_assets PUT
# (see docs/voog-mcp-endpoint-coverage.md). Wrapping in {"layout": …}
# happens to be tolerated for layouts, but {"layout_asset": …} is silently
# 200-ed by Voog and the asset content is NOT persisted (issue #96).
# Send flat for both to stay aligned with the docs and the MCP tool path.
_PAYLOAD = {
    "layout": ("/layouts", "body"),
    "asset": ("/layout_assets", "data"),
}


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
        endpoint_info = _PAYLOAD.get(kind)
        if endpoint_info is None:
            sys.stderr.write(f"  ✗ {rel_path}: unknown manifest type {kind!r}\n")
            failed += 1
            continue
        path_prefix, content_field = endpoint_info
        result = client.put(f"{path_prefix}/{entry['id']}", {content_field: body})
        # Silent-no-op detector (issue #96): Voog's wrapped-payload bug
        # returned 200 with the resource echoed back but with the content
        # field cleared. Surface that pattern as a hard failure rather
        # than printing ✓.  The check is deliberately narrow — we only
        # flag the case where the server explicitly echoed the field as
        # empty; a slim response that omits the field altogether is left
        # alone (some endpoints/versions may not echo).
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
