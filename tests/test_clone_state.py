"""Tests for voog.clone.state — the property the whole clone rests on.

Every phase decides what to skip by reading these maps. If they lose an
entry, the phase redoes work that is already on the target: duplicate pages,
re-uploaded images consuming a quota that has no room left. If they gain a
wrong entry, content is written over the wrong object on a live site.
"""

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from voog.clone.state import MANIFEST_NAME, CloneState, CloneStateError


def _run_with_deadline(fn, seconds: float = 10.0):
    """Run ``fn`` on a thread and fail loudly if it does not return.

    A deadlock in CloneState does not fail a test — it HANGS it, and a hung
    suite reads as an infrastructure problem rather than a bug. It happened:
    ``put`` held a non-reentrant lock and then called ``load``, which took
    the same lock, so the first recorded mapping never returned. Every test
    that writes state goes through this watchdog so the next such deadlock
    is a red test with a name.
    """
    box = {}

    def _target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(
            f"CloneState operation did not return within {seconds}s — deadlock. "
            "The maps are written from parallel upload workers, so a lock that "
            "is not reentrant hangs the whole clone on its first asset."
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


class TestMapRoundTrip(unittest.TestCase):
    def test_int_and_str_keys_are_the_same_entry(self):
        # JSON object keys are strings, so a map written with int keys reads
        # back with string ones. A phase that wrote map[2851128] before a
        # restart and looked up map["2851128"] after would miss every entry
        # it had recorded — and create all of them a second time.
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))
            _run_with_deadline(lambda: state.put("page_map", 2851128, 3550815))
            self.assertTrue(state.has("page_map", "2851128"))
            self.assertEqual(state.get("page_map", 2851128), 3550815)

            reopened = CloneState(Path(tmp))
            self.assertTrue(reopened.has("page_map", 2851128))
            self.assertEqual(reopened.get("page_map", "2851128"), 3550815)

    def test_each_put_is_flushed_immediately(self):
        # A crash after 400 of 617 uploads must not lose those 400 — they
        # already consumed the target's quota and cannot be reclaimed.
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))
            _run_with_deadline(lambda: state.put("asset_map", 1, {"id": 10}))
            on_disk = json.loads((Path(tmp) / "asset_map.json").read_text())
            self.assertEqual(on_disk, {"1": {"id": 10}})

    def test_put_many_flushes_once_and_keeps_prior_entries(self):
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))
            _run_with_deadline(lambda: state.put("layout_map", 1, 100))
            _run_with_deadline(lambda: state.put_many("layout_map", {2: 200, 3: 300}))
            on_disk = json.loads((Path(tmp) / "layout_map.json").read_text())
            self.assertEqual(on_disk, {"1": 100, "2": 200, "3": 300})

    def test_put_many_with_nothing_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))
            _run_with_deadline(lambda: state.put_many("layout_map", {}))
            self.assertFalse((Path(tmp) / "layout_map.json").exists())

    def test_missing_map_reads_as_empty(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(CloneState(Path(tmp)).load("nothing_here"), {})


class TestCorruptState(unittest.TestCase):
    def test_unreadable_map_raises_rather_than_reading_as_empty(self):
        # Treating a corrupt map as empty is the dangerous failure: it would
        # silently redo everything the map recorded.
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "asset_map.json").write_text("{ this is not json")
            state = CloneState(Path(tmp))
            with self.assertRaises(CloneStateError) as ctx:
                state.load("asset_map")
            self.assertIn("refusing", str(ctx.exception).lower())

    def test_a_json_list_where_a_map_belongs_reads_as_empty(self):
        # Valid JSON of the wrong shape carries no id mappings to lose, so
        # this one is safe to start over from.
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "page_map.json").write_text("[1, 2, 3]")
            self.assertEqual(CloneState(Path(tmp)).load("page_map"), {})


class TestBinding(unittest.TestCase):
    def _bind(self, tmp, source, target):
        state = CloneState(Path(tmp))
        state.bind(
            source=source, target=target, source_host=f"{source}.tld", target_host=f"{target}.tld"
        )
        return state

    def test_binding_writes_a_manifest(self):
        with TemporaryDirectory() as tmp:
            self._bind(tmp, "kolmkoma", "kolmkoma-2026")
            manifest = json.loads((Path(tmp) / MANIFEST_NAME).read_text())
            self.assertEqual(manifest["source"], "kolmkoma")
            self.assertEqual(manifest["target"], "kolmkoma-2026")
            self.assertIn("voog_mcp_version", manifest)

    def test_rebinding_the_same_pair_is_fine(self):
        # This is the resume path and must never be an error.
        with TemporaryDirectory() as tmp:
            self._bind(tmp, "a-site.tld", "b-site.tld")
            self._bind(tmp, "a-site.tld", "b-site.tld")

    def test_a_different_target_is_refused(self):
        # The worst failure this module can have: the maps name real target
        # ids, and pointing them at another site would overwrite its pages.
        with TemporaryDirectory() as tmp:
            self._bind(tmp, "kolmkoma", "kolmkoma-2026")
            state = CloneState(Path(tmp))
            with self.assertRaises(CloneStateError) as ctx:
                state.bind(
                    source="kolmkoma",
                    target="stella",
                    source_host="a",
                    target_host="b",
                )
            self.assertIn("different clone", str(ctx.exception))

    def test_a_different_source_is_refused(self):
        with TemporaryDirectory() as tmp:
            self._bind(tmp, "kolmkoma", "kolmkoma-2026")
            state = CloneState(Path(tmp))
            with self.assertRaises(CloneStateError):
                state.bind(
                    source="stella", target="kolmkoma-2026", source_host="a", target_host="b"
                )

    def test_unreadable_manifest_is_refused(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / MANIFEST_NAME).write_text("nope{")
            state = CloneState(Path(tmp))
            with self.assertRaises(CloneStateError):
                state.bind(source="a", target="b", source_host="x", target_host="y")


class TestPhaseBookkeeping(unittest.TestCase):
    def test_phase_summaries_survive_a_reopen(self):
        with TemporaryDirectory() as tmp:
            _run_with_deadline(
                lambda: CloneState(Path(tmp)).mark_phase_done("layouts", {"created": 24})
            )
            self.assertEqual(CloneState(Path(tmp)).phase_summary("layouts"), {"created": 24})

    def test_unknown_phase_summary_is_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(CloneState(Path(tmp)).phase_summary("articles"))


if __name__ == "__main__":
    unittest.main()


class TestConcurrentWrites(unittest.TestCase):
    """The asset phase records mappings from parallel upload workers.

    These two tests are the ones that would have caught the non-reentrant
    lock immediately, with a name, instead of hanging the suite.
    """

    def test_a_single_put_returns(self):
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))
            _run_with_deadline(lambda: state.put("asset_map", 1, {"id": 10}))
            self.assertEqual(state.get("asset_map", 1), {"id": 10})

    def test_parallel_writers_all_land(self):
        with TemporaryDirectory() as tmp:
            state = CloneState(Path(tmp))

            def _write_range(start):
                for i in range(start, start + 25):
                    state.put("asset_map", i, {"id": i * 10})

            def _all():
                threads = [threading.Thread(target=_write_range, args=(s,)) for s in (0, 100, 200)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(10)
                return [t.is_alive() for t in threads]

            still_running = _run_with_deadline(_all, seconds=30)
            self.assertEqual(still_running, [False, False, False])

            # Every mapping is present AND on disk — a lost entry means the
            # phase re-uploads an asset that already consumed target quota.
            reopened = CloneState(Path(tmp))
            for start in (0, 100, 200):
                for i in range(start, start + 25):
                    self.assertEqual(reopened.get("asset_map", i), {"id": i * 10})
