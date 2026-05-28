"""Capture site_snapshot baseline from a live Voog site (Phase 5 live capture).

Usage:
    source .venv/bin/activate
    STELLA_OLD_API_KEY=... python scripts/capture_snapshot_baseline.py \
        --host stellasoomlais.voog.com --token-env STELLA_OLD_API_KEY

Runs the current ``site_snapshot`` tool against the live site, then records a
compact summary of what was written: file list with sizes, the ``skipped``
list, total duration, total request count. The raw snapshot output is NOT
committed — only the summary lands in ``tests/fixtures/snapshot/``.

Purpose: anchor Phase 5's ``_meta.json`` design against the actual file set
that ``site_snapshot`` writes against a real Voog site today. Without this,
the manifest schema's ``attempted`` / ``succeeded`` / ``skipped`` lists are
specified against an imagined site that may or may not match how Voog
actually behaves (e.g. which endpoints 404 on an old archive site, which
ones return empty lists, which singletons exist).

Defaults to Stella OLD (stellasoomlais.voog.com) — Stella's archived site,
which has the full endpoint surface but isn't customer-facing, so a
slow/large snapshot here is consequence-free.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src/ so this script runs without `pip install -e .`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voog.client import VoogClient  # noqa: E402
from voog.mcp.tools import snapshot as snapshot_mod  # noqa: E402


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _summarise_dir(out: Path) -> dict:
    files: list[dict] = []
    total_bytes = 0
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        size = p.stat().st_size
        total_bytes += size
        files.append({"name": p.relative_to(out).as_posix(), "size_bytes": size})
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_size_human": _human_size(total_bytes),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="stellasoomlais.voog.com")
    parser.add_argument("--token-env", default="STELLA_OLD_API_KEY")
    parser.add_argument(
        "--output-fixture",
        default="tests/fixtures/snapshot/stella_old_pre_phase_5.json",
        help="Where to write the summary fixture (relative to repo root).",
    )
    parser.add_argument(
        "--keep-raw",
        action="store_true",
        help="Keep the raw snapshot dir at /tmp/voog-mcp-baseline-snap (debug).",
    )
    args = parser.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        print(f"ERROR: ${args.token_env} not set in environment", file=sys.stderr)
        return 2

    if args.keep_raw:
        snap_dir = Path("/tmp/voog-mcp-baseline-snap")
        if snap_dir.exists():
            shutil.rmtree(snap_dir)
        tmp_ctx = None
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="voog-mcp-baseline-")
        snap_dir = Path(tmp_ctx.name) / "snap"

    print(f"[*] site_snapshot against {args.host} → {snap_dir}")

    client = VoogClient(host=args.host, api_token=token)
    started = time.monotonic()
    try:
        result = snapshot_mod.call_tool(
            "site_snapshot",
            {"output_dir": str(snap_dir)},
            client,
        )
    finally:
        duration_s = time.monotonic() - started

    # Tool returns list[TextContent] | CallToolResult — both expose .text on items.
    if hasattr(result, "isError") and result.isError:
        print(f"[!] site_snapshot reported error in {duration_s:.1f}s", file=sys.stderr)
        for item in result.content:
            print(item.text, file=sys.stderr)
        return 3

    # Normalize: list[TextContent] (success path)
    items = result if isinstance(result, list) else result.content
    summary_text = items[0].text if items else ""
    body_json = json.loads(items[1].text) if len(items) > 1 else {}

    dir_summary = _summarise_dir(snap_dir)

    fixture = {
        "_meta": {
            "captured_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "+00:00"),
            "source_site": "stella-old",
            "voog_host": args.host,
            "captured_by": "scripts/capture_snapshot_baseline.py",
            "purpose": (
                "Pre-Phase-5 baseline: what site_snapshot writes today, before "
                "_meta.json is added. Use this to validate the manifest's "
                "attempted/succeeded/skipped lists against real-world Voog "
                "endpoint availability."
            ),
            "sanitisation": "none (no PII in directory listing or counts)",
        },
        "tool_summary_line": summary_text,
        "tool_result": body_json,
        "duration_seconds": round(duration_s, 3),
        "directory_summary": dir_summary,
    }

    out_path = ROOT / args.output_fixture
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[✓] {duration_s:.1f}s, {dir_summary['file_count']} files, "
          f"{dir_summary['total_size_human']}")
    print(f"[✓] {len(body_json.get('skipped', []))} skipped entries")
    print(f"[✓] fixture → {out_path}")

    if tmp_ctx is not None:
        tmp_ctx.cleanup()
    elif args.keep_raw:
        print(f"[i] raw snapshot kept at {snap_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
