"""voog push — upload modified template files to Voog."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from voog.client import VoogClient

# Endpoint dispatch by manifest entry type.  Both endpoints take a flat
# payload — wrapping {"layout_asset": …} is silently 200-ed without
# persisting (issue #96).  ``layout_asset`` is the legacy spelling
# written by pre-rename ``voog.py`` manifests; current ``voog pull``
# emits ``asset``.  Routing both to the same target keeps long-lived
# checkouts working without a forced re-pull.
_ENDPOINT = {
    "layout": ("/layouts", "body"),
    "asset": ("/layout_assets", "data"),
    "layout_asset": ("/layout_assets", "data"),
}
_ASSET_KINDS = {"asset", "layout_asset"}


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
    manifest_dirty = False
    for rel_path in targets:
        entry = manifest[rel_path]
        body = (local_dir / rel_path).read_text(encoding="utf-8")
        kind = entry["type"]
        endpoint = _ENDPOINT.get(kind)
        if endpoint is None:
            sys.stderr.write(f"  ✗ {rel_path}: unknown manifest type {kind!r}\n")
            failed += 1
            continue
        path_prefix, content_field = endpoint
        result = client.put(f"{path_prefix}/{entry['id']}", {content_field: body})
        err = _verify_persisted(kind, body, entry, result)
        if err:
            sys.stderr.write(f"  ✗ {rel_path}: {err}\n")
            failed += 1
            continue
        # Refresh the manifest's updated_at so a second push without an
        # intervening pull still has a fresh anchor for the next layout
        # verification. Also normalize the legacy "layout_asset" type
        # here — successive pushes self-heal the manifest without
        # requiring a forced re-pull.
        if isinstance(result, dict) and result.get("updated_at"):
            entry["updated_at"] = result["updated_at"]
            manifest_dirty = True
        if kind == "layout_asset":
            entry["type"] = "asset"
            manifest_dirty = True
        print(f"  ✓ {rel_path}")
    if manifest_dirty:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return 2 if failed else 0


def _verify_persisted(kind: str, body: str, entry: dict, result) -> str | None:
    """Return an error message if the PUT response contradicts a successful
    persist, else None. Voog's PUT responses are slim — the content field
    is omitted, so we rely on indirect signals: `size` for assets and
    `updated_at` for layouts. Each check falls through when the signal is
    missing from the response (or, for layouts, from the manifest), so we
    don't false-positive against older endpoints / hand-crafted manifests.
    """
    if not isinstance(result, dict):
        return None
    if kind in _ASSET_KINDS:
        # Voog's `size` field counts UTF-8 *characters*, not bytes —
        # empirically verified post-1.2.1 release (any file with a
        # non-ASCII char like an em-dash or Estonian õ otherwise produced
        # a false-positive ✗).  Compare against str length, not the
        # encoded byte count.
        sent_chars = len(body)
        stored_size = result.get("size")
        if stored_size is not None and stored_size != sent_chars:
            return (
                f"stored size {stored_size} does not match local "
                f"{sent_chars} characters — content NOT updated on Voog"
            )
    elif kind == "layout":
        prev = _parse_iso8601(entry.get("updated_at"))
        new = _parse_iso8601(result.get("updated_at"))
        if prev and new and new <= prev:
            return (
                f"updated_at did not advance ({result.get('updated_at')}) — "
                "content NOT updated on Voog"
            )
    return None


def _parse_iso8601(value) -> datetime | None:
    """Best-effort ISO 8601 parse. Voog returns timestamps like
    ``2026-05-01T10:01:17.806Z``. Returns None on anything we can't parse —
    callers must treat None as "no signal" and fall through rather than
    flagging a no-op. Avoids string comparison foot-guns when the server
    and the manifest disagree on fractional-second precision.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
