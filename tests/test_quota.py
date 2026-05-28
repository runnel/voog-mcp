"""Tests for voog.quota — per-site daily counter (S-7, R5)."""

import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog import quota
from voog.errors import DailyQuotaExceeded


class _TmpQuotaPath:
    """Context manager: redirect quota.json to a temp path."""

    def __init__(self):
        self._tmpdir = TemporaryDirectory()

    def __enter__(self) -> Path:
        path = Path(self._tmpdir.name) / "quota.json"
        self._patch = patch.dict(os.environ, {"VOOG_QUOTA_PATH": str(path)})
        self._patch.start()
        return path

    def __exit__(self, *_exc):
        self._patch.stop()
        self._tmpdir.cleanup()


class TestQuotaPath(unittest.TestCase):
    def test_default_path_uses_platformdirs(self):
        # Sanity-check the platformdirs integration without writing.
        os.environ.pop("VOOG_QUOTA_PATH", None)
        path = quota.quota_file_path()
        self.assertEqual(path.name, "quota.json")
        # Cache dir for "voog-mcp" — on macOS contains "Library/Caches".
        # The literal path varies by OS; assert only the suffix.
        self.assertIn("voog-mcp", str(path))

    def test_macos_path_shape(self):
        # If running on macOS, the cache path includes the documented
        # ``~/Library/Caches/voog-mcp/quota.json`` form. Skip elsewhere.
        import sys

        if sys.platform != "darwin":
            self.skipTest("macOS-specific path shape")
        os.environ.pop("VOOG_QUOTA_PATH", None)
        path = quota.quota_file_path()
        self.assertTrue(
            str(path).endswith("/Library/Caches/voog-mcp/quota.json"),
            f"expected macOS cache path, got {path}",
        )


class TestFirstRunContract(unittest.TestCase):
    """R5: file missing → 0; missing entry → 0; stale date → 0;
    malformed → 0 + WARN."""

    def test_file_missing_count_is_zero(self):
        with _TmpQuotaPath() as path:
            self.assertFalse(path.exists())
            self.assertEqual(quota.read_count("stella"), 0)

    def test_today_entry_missing_count_is_zero(self):
        with _TmpQuotaPath() as path:
            path.write_text(json.dumps({"date": quota.current_day_utc(), "sites": {"other": 5}}))
            self.assertEqual(quota.read_count("stella"), 0)
            # Sibling site preserved.
            self.assertEqual(quota.read_count("other"), 5)

    def test_stale_date_count_resets(self):
        with _TmpQuotaPath() as path:
            path.write_text(json.dumps({"date": "1999-01-01", "sites": {"stella": 999}}))
            self.assertEqual(quota.read_count("stella"), 0)

    def test_malformed_json_treated_as_zero(self):
        with _TmpQuotaPath() as path:
            path.write_text("{not valid json")
            with self.assertLogs("voog.quota", level="WARNING"):
                self.assertEqual(quota.read_count("stella"), 0)

    def test_non_object_json_treated_as_zero(self):
        with _TmpQuotaPath() as path:
            path.write_text("[1, 2, 3]")
            with self.assertLogs("voog.quota", level="WARNING"):
                self.assertEqual(quota.read_count("stella"), 0)

    def test_negative_count_treated_as_zero(self):
        # Defensive: hand-edited file with negative value.
        with _TmpQuotaPath() as path:
            path.write_text(json.dumps({"date": quota.current_day_utc(), "sites": {"stella": -5}}))
            self.assertEqual(quota.read_count("stella"), 0)


class TestIncrement(unittest.TestCase):
    def test_increment_from_zero(self):
        with _TmpQuotaPath():
            self.assertEqual(quota.increment("stella", None), 1)
            self.assertEqual(quota.read_count("stella"), 1)

    def test_increment_persists_through_module_calls(self):
        with _TmpQuotaPath():
            quota.increment("stella", None)
            quota.increment("stella", None)
            quota.increment("stella", None)
            self.assertEqual(quota.read_count("stella"), 3)

    def test_increment_isolates_sites(self):
        with _TmpQuotaPath():
            quota.increment("stella", None)
            quota.increment("stella", None)
            quota.increment("runnel", None)
            self.assertEqual(quota.read_count("stella"), 2)
            self.assertEqual(quota.read_count("runnel"), 1)

    def test_increment_no_limit_never_raises(self):
        with _TmpQuotaPath():
            for _ in range(50):
                quota.increment("stella", None)
            self.assertEqual(quota.read_count("stella"), 50)

    def test_increment_raises_when_over_limit(self):
        with _TmpQuotaPath():
            quota.increment("stella", limit=3)  # 1
            quota.increment("stella", limit=3)  # 2
            quota.increment("stella", limit=3)  # 3
            with self.assertRaises(DailyQuotaExceeded) as ctx:
                quota.increment("stella", limit=3)  # would-be 4
            self.assertIn("stella", str(ctx.exception))
            self.assertIn("daily_request_quota=3", str(ctx.exception))
            # Counter must NOT have advanced past the limit.
            self.assertEqual(quota.read_count("stella"), 3)

    def test_increment_resets_on_stale_date(self):
        with _TmpQuotaPath() as path:
            path.write_text(json.dumps({"date": "1999-01-01", "sites": {"stella": 99}}))
            # New day's first increment seeds 1, not 100.
            self.assertEqual(quota.increment("stella", None), 1)


class TestAtomicWriteUnderConcurrency(unittest.TestCase):
    """The atomic-rename property: under N threads incrementing in
    parallel, the file must remain parseable as JSON at every observable
    moment (no partial writes visible to readers). Drop-rate under the
    read-modify-write window is NOT asserted — by design (see module
    docstring); the assertion is "no torn writes"."""

    def test_concurrent_increments_keep_file_parseable(self):
        with _TmpQuotaPath() as path:
            n_threads = 8
            iters_per_thread = 25

            def worker():
                for _ in range(iters_per_thread):
                    quota.increment("stella", None)

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # File MUST be parseable JSON at the end (no torn final write).
            raw = json.loads(path.read_text())
            self.assertEqual(raw["date"], quota.current_day_utc())
            # Final count is between max(1) and n_threads*iters_per_thread.
            # Lower bound: at minimum one increment landed.
            self.assertGreater(raw["sites"]["stella"], 0)
            self.assertLessEqual(raw["sites"]["stella"], n_threads * iters_per_thread)


class TestCurrentDayUtc(unittest.TestCase):
    def test_format_is_iso_date(self):
        d = quota.current_day_utc()
        # YYYY-MM-DD, exactly 10 chars.
        self.assertEqual(len(d), 10)
        self.assertEqual(d[4], "-")
        self.assertEqual(d[7], "-")


if __name__ == "__main__":
    unittest.main()
