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
        err = _verify_persisted(kind, body, entry, result)
        if err:
            sys.stderr.write(f"  ✗ {rel_path}: {err}\n")
            failed += 1
            continue
        print(f"  ✓ {rel_path}")
    return 2 if failed else 0


def _verify_persisted(kind: str, body: str, entry: dict, result) -> str | None:
    """Return an error message if the PUT response contradicts a successful
    persist, else None. Voog's PUT responses are slim — the content field
    is omitted, so we rely on indirect signals: `size` for assets and
    `updated_at` for layouts. Each check is opt-in: if the signal is
    missing from the response (or, for layouts, from the manifest), we
    fall through rather than false-positive.

    Issue #96 follow-up: post-merge probing showed the original detector
    that watched for an echoed-empty content field never trips against
    real Voog responses, because the field is never echoed back at all.
    """
    if not isinstance(result, dict):
        return None
    # Belt-and-suspenders: the original #96 report described the response
    # echoing the content field as empty string. Real Voog responses are
    # slim and don't echo it at all (verified post-merge), so this check
    # is dead today — kept anyway, since it costs nothing and matches the
    # user-observed symptom.
    field = "data" if kind == "asset" else "body" if kind == "layout" else None
    if body and field and field in result and not result[field]:
        return f"PUT returned 200 but stored {field!r} is empty — content NOT updated on Voog"
    if kind == "asset":
        # Voog includes byte size in the slim response — the rock-solid
        # signal that the new content was actually stored.
        sent_bytes = len(body.encode("utf-8"))
        stored_size = result.get("size")
        if stored_size is not None and stored_size != sent_bytes:
            return (
                f"stored size {stored_size} does not match local "
                f"{sent_bytes} bytes — content NOT updated on Voog"
            )
    elif kind == "layout":
        # Layouts response includes `updated_at` (no body). Compare
        # against manifest's stored timestamp; if it didn't advance, the
        # layout content didn't change.
        prev = entry.get("updated_at") or None
        new = result.get("updated_at") or None
        if prev and new and new <= prev:
            return f"updated_at did not advance ({new}) — content NOT updated on Voog"
    return None
