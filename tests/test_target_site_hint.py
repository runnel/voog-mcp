"""Tests for S-8 target_site carrier on VoogClient (thread-local + fallback)."""

import threading
import unittest

from voog.client import VoogClient


class TestTargetSiteCarrier(unittest.TestCase):
    def test_initial_target_is_none(self):
        c = VoogClient(host="example.com", api_token="t")
        self.assertIsNone(c.get_target_site())

    def test_set_then_get_same_thread(self):
        c = VoogClient(host="example.com", api_token="t")
        c.set_target_site("stella")
        self.assertEqual(c.get_target_site(), "stella")

    def test_set_none_clears(self):
        c = VoogClient(host="example.com", api_token="t")
        c.set_target_site("stella")
        c.set_target_site(None)
        # Thread-local slot is now None — falls back to instance attr,
        # which set_target_site also set to None.
        self.assertIsNone(c.get_target_site())

    def test_thread_isolation_thread_local_wins(self):
        c = VoogClient(host="example.com", api_token="t")
        c.set_target_site("stella")

        seen: dict[str, str | None] = {}

        def worker():
            # Worker thread has no _local.last_site of its own — falls
            # back to the instance _last_site (set by main thread).
            seen["worker_before"] = c.get_target_site()
            c.set_target_site("runnel")
            seen["worker_after"] = c.get_target_site()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)

        # Main thread's _local.last_site is still "stella" (thread-local).
        # Instance _last_site has been overwritten by worker to "runnel"
        # — but main thread's _local.last_site takes precedence in
        # get_target_site, so main reads "stella".
        self.assertEqual(c.get_target_site(), "stella")
        self.assertEqual(seen["worker_after"], "runnel")
        # Before worker set its own slot, it read the instance fallback —
        # which was "stella" at that moment (set by main).
        self.assertEqual(seen["worker_before"], "stella")

    def test_cross_thread_fallback_when_thread_local_unset(self):
        # Fresh worker thread, no main-thread tls — only the instance
        # fallback path is exercised.
        c = VoogClient(host="example.com", api_token="t")
        c._last_site = "stella"  # simulate cross-thread set

        seen = {}

        def worker():
            seen["site"] = c.get_target_site()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)
        self.assertEqual(seen["site"], "stella")


if __name__ == "__main__":
    unittest.main()
