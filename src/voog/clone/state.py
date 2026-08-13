"""Durable, resumable state for a cross-site clone.

A clone is a long sequence of writes against a live API, and it *will* be
interrupted: an asset quota fills up mid-upload, a 502 lands on article 41
of 60, the operator stops it. The reference run this module generalises
(`duplicate.py`, kolmkoma.ee → kolm-koma-2026, 2026-08-04) survived exactly
those and finished only because every phase could be re-entered.

The contract each map upholds:

  **source id → target id, written the moment the target exists.**

That single shape is what makes the phases idempotent. A phase re-run skips
every source object already in its map, so "run it again" is always safe and
never duplicates. It is also why the maps are flushed continuously rather
than at the end of a phase: a crash after 400 of 617 uploads must not
re-upload those 400 into a quota that no longer has room for them.

Keys are **strings** because JSON object keys are strings — reading a map
back gives ``{"2851128": 3550815}``, and a phase that looked up ``map[2851128]``
after a resume would miss every entry it wrote before the restart while
finding them all in the same process. :meth:`CloneState.get` /
:meth:`CloneState.put` coerce, so callers can pass either.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from threading import RLock

logger = logging.getLogger("voog.clone")

# Written next to the maps so a state directory is self-describing: which
# two sites it belongs to, and which voog-mcp wrote it. Pointing a resume at
# the state of a DIFFERENT pair of sites would map source ids onto unrelated
# target ids and quietly overwrite live content, so the pairing is checked
# rather than assumed.
MANIFEST_NAME = "_clone.json"


class CloneStateError(RuntimeError):
    """State directory is unusable or belongs to a different clone."""


def _atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON via a uniquely-named sibling tmp + ``os.replace``.

    Same idiom as :mod:`voog.quota`: the tmp name carries PID + uuid so two
    threads flushing different maps (or the same map) cannot pull a shared
    tmp file out from under each other, and ``os.replace`` is atomic on the
    same filesystem — a crash mid-write leaves the previous good state, not
    a truncated file that would read as "nothing done yet" and re-run work
    that already happened.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover — best effort
                pass


class CloneState:
    """Named JSON maps under one directory, flushed on every write.

    Thread-safe: the asset phase uploads in parallel and every worker
    records its result here.
    """

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self._maps: dict[str, dict] = {}
        # RLock, not Lock: `put` holds the lock and then calls `load`, which
        # takes it again. With a plain Lock that is an unconditional deadlock
        # on the FIRST recorded mapping — the asset phase would hang forever
        # on its first upload, and a deadlock does not fail a test, it hangs
        # one. tests/test_clone_state.py now runs every write through a
        # watchdog so the next such mistake is a red test with a name.
        self._lock = RLock()

    # ---------------------------------------------------------- lifecycle
    def bind(self, *, source: str, target: str, source_host: str, target_host: str) -> None:
        """Claim this directory for one (source, target) pair, or refuse it.

        Resuming into the wrong directory is the worst failure this module
        can have: the maps would name real target ids that belong to a
        different site, and the contents phase would happily overwrite them.
        Cheap to check, unrecoverable to get wrong.
        """
        from voog import __version__

        path = self.dir / MANIFEST_NAME
        want = {"source": source, "target": target}
        if path.exists():
            try:
                have = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CloneStateError(
                    f"state directory {self.dir} has an unreadable {MANIFEST_NAME}: {exc}. "
                    "Delete the directory to start a fresh clone, or point --state-dir "
                    "somewhere else."
                ) from exc
            for key, expected in want.items():
                actual = have.get(key)
                if actual is not None and actual != expected:
                    raise CloneStateError(
                        f"state directory {self.dir} belongs to a different clone "
                        f"({have.get('source')} -> {have.get('target')}, "
                        f"asked for {source} -> {target}). Resuming here would map "
                        "source ids onto another site's objects. Use a fresh "
                        "--state-dir."
                    )
        _atomic_write_json(
            path,
            {
                "source": source,
                "target": target,
                "source_host": source_host,
                "target_host": target_host,
                "voog_mcp_version": __version__,
            },
        )

    # --------------------------------------------------------------- maps
    def load(self, name: str) -> dict:
        """Return the named map, reading it from disk on first use."""
        with self._lock:
            if name not in self._maps:
                path = self.dir / f"{name}.json"
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        # A corrupt map is NOT treated as empty: that would
                        # silently redo every object it recorded, which for
                        # the asset phase means re-uploading into a quota
                        # that has no room, and for pages means duplicates.
                        raise CloneStateError(
                            f"state file {path} is unreadable ({exc}). Refusing to "
                            "treat it as empty — that would redo work it recorded. "
                            "Inspect or delete the file deliberately."
                        ) from exc
                    self._maps[name] = data if isinstance(data, dict) else {}
                else:
                    self._maps[name] = {}
            return self._maps[name]

    def get(self, name: str, key) -> object | None:
        return self.load(name).get(str(key))

    def has(self, name: str, key) -> bool:
        return str(key) in self.load(name)

    def put(self, name: str, key, value) -> None:
        """Record one mapping and flush immediately.

        Flushing per entry rather than per phase is deliberate. The cost is
        one small write per created object; the alternative loses everything
        since the last checkpoint, and for the asset phase that is
        irreversible — the uploads are already on the target, consuming
        quota, but unreachable because nothing knows their ids.
        """
        with self._lock:
            data = self.load(name)
            data[str(key)] = value
            _atomic_write_json(self.dir / f"{name}.json", data)

    def put_many(self, name: str, entries: dict) -> None:
        """Record several mappings in one flush (bulk phases)."""
        if not entries:
            return
        with self._lock:
            data = self.load(name)
            for key, value in entries.items():
                data[str(key)] = value
            _atomic_write_json(self.dir / f"{name}.json", data)

    def mark_phase_done(self, phase: str, summary: dict) -> None:
        self.put("_phases", phase, summary)

    def phase_summary(self, phase: str) -> dict | None:
        value = self.get("_phases", phase)
        return value if isinstance(value, dict) else None
