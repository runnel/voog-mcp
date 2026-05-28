"""Per-site daily request-quota counter for voog-mcp.

Persistence model:

- Counter state lives in a single JSON file at
  ``platformdirs.user_cache_dir("voog-mcp")/quota.json``. On macOS this
  resolves to ``~/Library/Caches/voog-mcp/quota.json``; Linux gets
  ``~/.cache/voog-mcp/quota.json``; Windows gets
  ``%LOCALAPPDATA%\\voog-mcp\\Cache\\quota.json``.
- File shape: ``{"date": "YYYY-MM-DD", "sites": {"<site>": <int>}}``.
  ``date`` is the UTC calendar day the counts apply to. ``sites``
  is a flat map of site name to request count for that day.
- Counters reset at UTC midnight — the first read on a new day sees
  the stale ``date`` and starts fresh.
- Writes go through an atomic rename (``json.dump`` to a sibling tmp
  path, then ``os.replace``) so concurrent ``parallel_map`` fan-outs
  inside one snapshot don't shred the file. POSIX guarantees
  ``os.replace`` is atomic on the same filesystem.

R5 (first-run contract):

- **File missing:** treated as count 0. ``increment`` creates the
  parent directory and writes a fresh file.
- **File exists, today's date but no entry for this site:** site
  starts at 0. Other sites' counters preserved.
- **File exists, stale date:** counter reset across all sites; new
  date adopted on the next write.
- **Malformed JSON:** logged at WARNING level, treated as 0,
  overwritten by the next ``increment`` call. The malformed file is
  NOT preserved — recovery prefers progress over forensics.

Concurrency model:

- ``increment`` reads the file, mutates the in-memory dict, writes
  via atomic rename. Two threads racing the same site CAN drop one
  increment under the read-modify-write window. This is acceptable —
  the quota is a coarse safety rail, not a hard accounting boundary
  — and the worst-case drift is bounded by ``len(fan-out)``, which
  for snapshot's ``max_workers=8`` is at most 7 lost increments per
  burst. Tests cover the atomic-rename property; tests do NOT assert
  zero-drop under concurrency.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import platformdirs

logger = logging.getLogger("voog.quota")

_APP_NAME = "voog-mcp"
_QUOTA_FILENAME = "quota.json"


def current_day_utc() -> str:
    """Return today's UTC date as ISO-8601 ``YYYY-MM-DD``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def quota_file_path() -> Path:
    """Return the absolute path to the quota state file.

    Resolved fresh on every call so test patches of
    ``platformdirs.user_cache_dir`` (or the ``VOOG_QUOTA_PATH``
    override below) take effect immediately. ``VOOG_QUOTA_PATH``
    is a test-only override and is NOT documented for end users.
    """
    override = os.environ.get("VOOG_QUOTA_PATH")
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME)) / _QUOTA_FILENAME


def _read_state() -> dict:
    """Return the parsed quota state, or an empty dict if missing/malformed.

    Empty dict signals "no state yet" — the caller treats it as
    "all counters are 0 for today" without further branching.
    Malformed files log a WARNING and are returned as empty.
    """
    path = quota_file_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "quota file at %s is malformed (%s) — treating as 0, will overwrite",
            path,
            exc,
        )
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "quota file at %s is not a JSON object (got %s) — treating as 0",
            path,
            type(raw).__name__,
        )
        return {}
    return raw


def _atomic_write(state: dict) -> None:
    """Write *state* to the quota file via atomic rename.

    Creates the parent directory if missing. The tmp file is a
    sibling of the target so ``os.replace`` stays on the same
    filesystem (POSIX atomicity requirement).

    Tmp filename includes PID + ``uuid4().hex[:8]`` so concurrent writers
    don't collide on the tmp path. Without uniqueness, two threads racing
    the same path would: T1 writes ``quota.json.tmp``, T1 replaces →
    tmp gone; T2 writes ``quota.json.tmp``, T2 replaces → ok. But if
    T1's replace happens AFTER T2's open() and BEFORE T2's replace(),
    T2's replace() raises FileNotFoundError because T1 just moved the
    shared tmp file out from under it.
    """
    path = quota_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    tmp = path.with_suffix(path.suffix + suffix)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def read_count(site_name: str) -> int:
    """Return the request count for *site_name* on today's UTC day.

    R5 contract: file missing → 0; today's date but no entry for the
    site → 0; stale date → 0 (counters logically reset, physical
    write happens on the next ``increment``); malformed JSON → 0.
    """
    state = _read_state()
    if state.get("date") != current_day_utc():
        return 0
    sites = state.get("sites", {})
    if not isinstance(sites, dict):
        return 0
    val = sites.get(site_name, 0)
    return val if isinstance(val, int) and val >= 0 else 0


def increment(site_name: str, limit: int | None) -> int:
    """Increment *site_name*'s counter and persist.

    Returns the new count. If *limit* is set and the **new** count
    would exceed *limit*, raises :class:`voog.errors.DailyQuotaExceeded`
    BEFORE persisting — the write happens only on the success path.

    Stale-date detection: if the on-disk ``date`` differs from
    ``current_day_utc()``, the on-disk ``sites`` dict is discarded
    and a fresh day is started; only *site_name* is non-zero in the
    new state.
    """
    from voog.errors import DailyQuotaExceeded  # avoid circular import

    today = current_day_utc()
    state = _read_state()
    if state.get("date") != today or not isinstance(state.get("sites"), dict):
        # Stale day (or malformed structure) — reset.
        state = {"date": today, "sites": {}}
    sites = state["sites"]
    current = sites.get(site_name, 0)
    if not isinstance(current, int) or current < 0:
        current = 0
    new_count = current + 1
    if limit is not None and new_count > limit:
        raise DailyQuotaExceeded(
            f"site {site_name!r} exceeded daily_request_quota={limit} "
            f"(current count {current} → would become {new_count}). "
            f"Counter resets at UTC midnight."
        )
    sites[site_name] = new_count
    _atomic_write(state)
    return new_count
