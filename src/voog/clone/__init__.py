"""Cross-site clone: apply one Voog site's content to another (issue #140 item 2).

``site_snapshot`` has read a whole site since v1.2; nothing applied one to
another, so a real duplication job (kolmkoma.ee → kolm-koma-2026, 2026-08-04:
55 layouts, 38 layout assets, 617 media files, 7 pages, 60 articles with
per-article galleries) ran on a one-off script instead of this package. This
module generalises that script.

**Why phases and not one call.** The reference run was interrupted twice —
once by the target's asset quota, once by a transient API error — and
finished only because each stage could be re-entered without redoing or
duplicating what came before. That property is the feature here, not an
implementation detail: every phase consults a persistent source-id → target-id
map before it writes, and records each mapping the moment the target object
exists. See :mod:`voog.clone.state`.

**Live, not from a snapshot.** The clone reads the source through its own
API token rather than from a `site_snapshot` directory. A snapshot does not
carry per-article contents, language-level contents, or layout-asset `data`
(the list endpoint dropped it in 2026-06), so a snapshot-driven clone would
silently omit every article body and gallery. Both sites are already in
`voog.json`; reading the live source removes a whole class of "your snapshot
was incomplete" failures.

**Dry run by default.** ``site_clone`` reports what it would do and changes
nothing unless ``force=true`` is passed. The tool overwrites a site — the
default has to be the harmless one.

What this does NOT do — stated plainly, because a clone that quietly omits
things is worse than one that says so:

  - **Ecommerce is not copied.** No products, variants, categories,
    discounts, cart rules or orders. Product images alone need the asset
    protocol plus the ordering fix, variants carry a destructive-PUT
    foot-gun, and orders are customer data that must never be duplicated.
  - **Elements are not copied** (element instances or definitions).
  - **Redirects, webhooks, forms and comments are not copied.**
  - **Languages are matched, never created.** Adding a language changes
    every URL on a site; the operator does that deliberately.
  - **`created_at` / `published_at` cannot be set** — Voog returns 200 and
    resets them to now. Copied articles carry the clone date.
  - **Duplicate article paths cannot be reproduced** — Voog auto-suffixes
    the twin.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from voog.clone.phases import (
    KNOWN_LIMITS,
    MUTATING_PHASES,
    PHASE_FUNCTIONS,
    PHASE_ORDER,
    CloneContext,
    PhaseReport,
    describe_exc,
    quota_of,
)
from voog.clone.state import CloneState, CloneStateError

logger = logging.getLogger("voog.clone")

__all__ = [
    "KNOWN_LIMITS",
    "MUTATING_PHASES",
    "PHASE_ORDER",
    "CloneContext",
    "CloneState",
    "CloneStateError",
    "PhaseReport",
    "quota_of",
    "resolve_phases",
    "run_clone",
]


def resolve_phases(requested: list | None) -> list:
    """Normalise a requested phase list into run order.

    ``None`` / empty means "the whole pipeline" — every phase in
    :data:`PHASE_ORDER`, which deliberately excludes ``plan`` (a preflight
    the caller asks for explicitly) . An explicit list is validated and
    **re-sorted into pipeline order**: phases depend on the id maps written
    by earlier ones, so honouring a caller's `["contents", "pages"]`
    literally would run contents against an empty page_map and report every
    page as missing.
    """
    if not requested:
        return list(PHASE_ORDER)
    unknown = [p for p in requested if p not in PHASE_FUNCTIONS]
    if unknown:
        raise ValueError(f"unknown phase(s) {unknown}. Available: {['plan', *PHASE_ORDER]}")
    if "plan" in requested:
        # plan is read-only and answers a different question; run it alone.
        return ["plan"]
    ordered = [p for p in PHASE_ORDER if p in requested]
    return ordered


def _refuse_self_clone(source, target, source_name: str, target_name: str) -> None:
    """Refuse a clone whose source and target are the same Voog site.

    The phases delete-and-rebuild the target's content areas, so this would
    destroy the very content it is reading — irreversibly, and before anyone
    noticed.

    Comparing configured hosts is not enough. Every Voog site answers on
    BOTH its own domain and its `*.voog.com` address, so two `voog.json`
    entries can name one site with two different `host` values and sail
    past a string comparison. The authority is the site itself: `GET /site`
    reports `public_url` and `primary_domain`, which are identical for any
    two aliases of one site.
    """
    if source_name == target_name:
        raise ValueError(
            f"source and target are the same site name ({source_name!r}). The clone "
            "rebuilds the target's content areas, so this would destroy the source "
            "it is reading from."
        )
    source_host = getattr(source, "host", None)
    target_host = getattr(target, "host", None)
    if source_host and source_host == target_host:
        raise ValueError(
            f"source {source_name!r} and target {target_name!r} both resolve to host "
            f"{target_host!r} — the same site. The clone rebuilds the target's "
            "content areas, so this would destroy the source it is reading from."
        )
    try:
        source_site = source.get("/site")
        target_site = target.get("/site")
    except Exception:
        # Identity could not be confirmed either way. The host check above
        # already passed; do not block a legitimate clone on a transient
        # read, but do not pretend it was verified either.
        logger.warning(
            "could not read /site on both sides to confirm %s and %s are different "
            "sites; proceeding on the host comparison alone",
            source_name,
            target_name,
        )
        return
    for field_name in ("public_url", "primary_domain"):
        source_value = (source_site or {}).get(field_name)
        target_value = (target_site or {}).get(field_name)
        if source_value and source_value == target_value:
            raise ValueError(
                f"source {source_name!r} ({source_host}) and target {target_name!r} "
                f"({target_host}) are the SAME Voog site — both report "
                f"{field_name}={source_value!r}. A Voog site answers on both its own "
                "domain and its *.voog.com address, so two config entries can name "
                "one site. The clone would destroy the source it is reading from."
            )


def run_clone(
    *,
    source,
    target,
    source_name: str,
    target_name: str,
    state_dir: str | Path,
    phases: list | None = None,
    dry_run: bool = True,
    asset_budget_bytes: int | None = None,
    max_workers: int = 4,
) -> dict:
    """Run the requested phases and return a structured report.

    Refuses to clone a site onto itself: the phases delete-and-rebuild
    content areas, so source == target would destroy the very content it is
    reading, and the failure would be irreversible before it was noticed.
    """
    _refuse_self_clone(source, target, source_name, target_name)

    state = CloneState(Path(state_dir))
    state.bind(
        source=source_name,
        target=target_name,
        source_host=getattr(source, "host", ""),
        target_host=getattr(target, "host", ""),
    )
    ctx = CloneContext(
        source=source,
        target=target,
        state=state,
        source_name=source_name,
        target_name=target_name,
        dry_run=dry_run,
        asset_budget_bytes=asset_budget_bytes,
        max_workers=max_workers,
    )

    selected = resolve_phases(phases)
    reports: list = []
    started = time.monotonic()
    for name in selected:
        phase_started = time.monotonic()
        logger.info("clone phase %s (%s -> %s)", name, source_name, target_name)
        try:
            report = PHASE_FUNCTIONS[name](ctx)
        except Exception as exc:
            # One phase failing must not discard the phases that already
            # succeeded — their state maps are on disk and the report has to
            # say how far the run got, or the operator cannot decide whether
            # to resume or start over.
            logger.exception("clone phase %s failed", name)
            failed = PhaseReport(name)
            failed.problem(f"phase {name}", f"aborted: {describe_exc(exc)}")
            failed.details["aborted"] = True
            reports.append(failed.to_dict())
            break
        payload = report.to_dict()
        payload["seconds"] = round(time.monotonic() - phase_started, 2)
        reports.append(payload)
        if not dry_run and name in MUTATING_PHASES:
            state.mark_phase_done(name, payload)

    problems = sum(len(r.get("problems") or []) for r in reports)
    aborted = any(r.get("aborted") for r in reports)
    return {
        "source": source_name,
        "target": target_name,
        "dry_run": dry_run,
        "state_dir": str(state_dir),
        "phases_requested": selected,
        "phases_run": [r["phase"] for r in reports],
        "aborted": aborted,
        "problem_count": problems,
        "seconds": round(time.monotonic() - started, 2),
        "reports": reports,
        "known_limits": list(KNOWN_LIMITS),
    }
