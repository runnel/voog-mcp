"""``voog site-clone`` — copy one configured site's content onto another.

CLI parity for the ``site_clone`` MCP tool (issue #140 item 2). The CLI is
the surface that matters most for a long run: a full clone is tens of
minutes of uploads, which is a poor fit for a chat turn but exactly what a
terminal (or a resumed terminal) is for.

Both clients are resolved through the CLI's own ``_build_client``, so the
target inherits the same token resolution, ``site_name`` and per-site daily
request quota as the source — the parity gap that v1.4 phase 7 found on
snapshots.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from voog.client import VoogClient
from voog.clone import PHASE_ORDER, CloneStateError, resolve_phases, run_clone

_ALL_PHASES = ("plan", *PHASE_ORDER)


def add_arguments(subparsers):
    parser = subparsers.add_parser(
        "site-clone",
        help="Copy --site's content onto --target (phased, resumable, dry run by default)",
        description=(
            "Copy layouts, layout assets, media, site settings, pages, content "
            "areas and articles from --site (source, read-only) onto --target "
            "(OVERWRITTEN). Dry run unless --force. Resume an interrupted run by "
            "passing the same --state-dir. Does NOT copy ecommerce, elements, "
            "redirects, webhooks, forms or comments."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        metavar="SITE",
        help="Target site name from voog.json — its content is overwritten",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        type=Path,
        help="Directory holding the resume state (source-id -> target-id maps)",
    )
    parser.add_argument(
        "--phases",
        default="",
        help=(
            "Comma-separated subset to run (default: the whole pipeline). "
            f"Available: {', '.join(_ALL_PHASES)}. 'plan' is a read-only "
            "preflight and runs alone."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually write to the target. Without it nothing is changed.",
    )
    parser.add_argument(
        "--asset-budget-bytes",
        type=int,
        default=None,
        help="Cap on bytes uploaded in the assets phase (default: target's remaining quota)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Parallel asset uploads, 1-8 (default 4)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    parser.set_defaults(func=cmd_site_clone)


def _build_target_client(args) -> VoogClient:
    """Resolve --target through the same path as the source client.

    Imported here rather than at module scope: ``voog.cli.main`` imports
    every command module to build the parser, so a top-level import back
    into it would be circular.
    """
    from voog.cli.main import _build_client

    target_args = copy.copy(args)
    target_args.site = args.target
    return _build_client(target_args)


def cmd_site_clone(args, client: VoogClient) -> int:
    source_name = getattr(args, "site", None) or getattr(client, "site_name", None) or "source"
    if not 1 <= args.max_workers <= 8:
        sys.stderr.write("error: --max-workers must be between 1 and 8\n")
        return 1

    phases = [p.strip() for p in (args.phases or "").split(",") if p.strip()]
    try:
        resolve_phases(phases or None)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    try:
        target = _build_target_client(args)
    except Exception as exc:
        sys.stderr.write(f"error: cannot resolve --target {args.target!r}: {exc}\n")
        return 1

    try:
        result = run_clone(
            source=client,
            target=target,
            source_name=source_name,
            target_name=args.target,
            state_dir=args.state_dir,
            phases=phases or None,
            dry_run=not args.force,
            asset_budget_bytes=args.asset_budget_bytes,
            max_workers=args.max_workers,
        )
    except (CloneStateError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    # Non-zero on abort so a cron / CI caller notices. Per-item problems do
    # NOT fail the run: a clone that copied 615 of 617 images because the
    # quota ran out did its job and reported the gap, and exiting non-zero
    # there would train the operator to ignore the exit code.
    return 1 if result.get("aborted") else 0


def _print_human(result: dict) -> None:
    mode = "DRY RUN — nothing written" if result["dry_run"] else "APPLIED"
    print(f"{result['source']} → {result['target']}  [{mode}]")
    print(f"state: {result['state_dir']}")
    print()
    for report in result.get("reports") or []:
        line = (
            f"  {report['phase']:<15} "
            f"created={report.get('created', 0):<5} "
            f"updated={report.get('updated', 0):<5} "
            f"skipped={report.get('skipped', 0):<5} "
            f"{report.get('seconds', 0)}s"
        )
        if report.get("aborted"):
            line += "  ABORTED"
        print(line)
        for note in report.get("notes") or []:
            print(f"      note: {note}")
        for problem in (report.get("problems") or [])[:20]:
            print(f"      ! {problem['what']}: {problem['reason']}")
        extra = len(report.get("problems") or []) - 20
        if extra > 0:
            print(f"      ! … and {extra} more (use --json for the full list)")
    print()
    if result.get("aborted"):
        print("ABORTED mid-run. Re-run with the same --state-dir to resume.")
    elif result["dry_run"]:
        print("Nothing was written. Re-run with --force to apply.")
    if result.get("problem_count"):
        print(f"{result['problem_count']} item(s) could not be copied — see the ! lines above.")
