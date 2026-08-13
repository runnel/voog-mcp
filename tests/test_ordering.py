"""Unit tests for voog._ordering.put_ordered_with_readback.

The loop exists because Voog returns 200 for a write it applied only
partially. Every test here is about that gap: the status code is never the
evidence, the read-back is.
"""

import unittest

from voog._ordering import put_ordered_with_readback


class _FakeEndpoint:
    """Voog-shaped stub: stores what it is given, but scrambles the order
    for the first ``bad_writes`` writes — the live failure mode."""

    def __init__(self, *, bad_writes: int = 0, scramble=None):
        self.bad_writes = bad_writes
        self.scramble = scramble or (lambda order: [order[1], order[0], *order[2:]])
        self.stored: list = []
        self.writes = 0
        self.reads = 0

    def put(self, order):
        self.writes += 1
        self.stored = self.scramble(list(order)) if self.writes <= self.bad_writes else list(order)
        return {"ok": True, "write": self.writes}

    def read(self):
        self.reads += 1
        return list(self.stored)


class TestHappyPath(unittest.TestCase):
    def test_a_write_that_lands_first_time_costs_one_put(self):
        ep = _FakeEndpoint()
        wanted = [1, 2, 3]
        result, verified, final = put_ordered_with_readback(
            put=lambda: ep.put(wanted), read_order=ep.read, wanted=wanted
        )
        self.assertTrue(verified)
        self.assertEqual(final, wanted)
        self.assertEqual(ep.writes, 1)
        self.assertEqual(result, {"ok": True, "write": 1})


class TestRetry(unittest.TestCase):
    def test_second_put_fixes_the_order_live_failure_shape(self):
        # 6 of 12 live product PUTs came back with one adjacent pair
        # transposed; the identical repeat fixed every one of them.
        ep = _FakeEndpoint(bad_writes=1)
        wanted = [10, 20, 30, 40]
        _, verified, final = put_ordered_with_readback(
            put=lambda: ep.put(wanted), read_order=ep.read, wanted=wanted
        )
        self.assertTrue(verified)
        self.assertEqual(final, wanted)
        self.assertEqual(ep.writes, 2)

    def test_gives_up_after_the_attempt_cap_and_reports_unverified(self):
        ep = _FakeEndpoint(bad_writes=99)
        wanted = [1, 2, 3]
        _, verified, final = put_ordered_with_readback(
            put=lambda: ep.put(wanted), read_order=ep.read, wanted=wanted, attempts=3
        )
        self.assertFalse(verified)
        self.assertEqual(ep.writes, 3)
        # The caller gets what Voog actually holds, so it can say so.
        self.assertEqual(final, [2, 1, 3])

    def test_attempts_below_one_still_writes_exactly_once(self):
        # A zero/negative cap must not turn the write into a no-op — that
        # would silently skip the update the caller asked for.
        ep = _FakeEndpoint()
        put_ordered_with_readback(
            put=lambda: ep.put([1]), read_order=ep.read, wanted=[1], attempts=0
        )
        self.assertEqual(ep.writes, 1)


class TestReadBackFailure(unittest.TestCase):
    def test_a_failing_read_is_unverified_not_an_error(self):
        # The write probably landed; turning a verification hiccup into a
        # raised error would be a worse lie than "could not confirm".
        writes = []

        def _read():
            raise RuntimeError("transient 502 on read-back")

        result, verified, final = put_ordered_with_readback(
            put=lambda: writes.append(1) or "wrote",
            read_order=_read,
            wanted=[1, 2],
        )
        self.assertEqual(result, "wrote")
        self.assertFalse(verified)
        self.assertEqual(final, [])
        # Exactly one write — a failed read must not trigger a retry storm
        # against an endpoint that may already be struggling.
        self.assertEqual(len(writes), 1)

    def test_write_exception_propagates(self):
        # A failed WRITE is a real failure and must reach the caller; only
        # read-back failures are forgiven.
        def _put():
            raise RuntimeError("422 quota_exceeded")

        with self.assertRaises(RuntimeError):
            put_ordered_with_readback(put=_put, read_order=lambda: [], wanted=[1])


class TestOrderIsComparedExactly(unittest.TestCase):
    def test_same_membership_wrong_sequence_is_not_verified(self):
        ep = _FakeEndpoint()
        ep.stored = [3, 2, 1]
        _, verified, _ = put_ordered_with_readback(
            put=lambda: None, read_order=ep.read, wanted=[1, 2, 3], attempts=1
        )
        self.assertFalse(verified)

    def test_extra_asset_left_behind_is_not_verified(self):
        ep = _FakeEndpoint()
        ep.stored = [1, 2, 3, 4]
        _, verified, _ = put_ordered_with_readback(
            put=lambda: None, read_order=ep.read, wanted=[1, 2, 3], attempts=1
        )
        self.assertFalse(verified)


if __name__ == "__main__":
    unittest.main()
