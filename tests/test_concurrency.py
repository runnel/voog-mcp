"""Tests for voog._concurrency.parallel_map."""

import random
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from voog._concurrency import parallel_map


class TestParallelMapEmpty(unittest.TestCase):
    def test_empty_list_returns_empty(self):
        self.assertEqual(parallel_map(lambda x: x * 2, []), [])

    def test_empty_list_does_not_spawn_pool(self):
        with patch("voog._concurrency.ThreadPoolExecutor") as mock_pool:
            result = parallel_map(lambda x: x, [])
        self.assertEqual(result, [])
        mock_pool.assert_not_called()


class TestParallelMapSingleItem(unittest.TestCase):
    def test_single_item_returns_one_tuple(self):
        results = parallel_map(lambda x: x * 2, [5])
        self.assertEqual(results, [(5, 10, None)])

    def test_single_item_skips_thread_pool(self):
        # Single-item lists run synchronously — no pool start/teardown.
        with patch("voog._concurrency.ThreadPoolExecutor") as mock_pool:
            results = parallel_map(lambda x: x * 2, [5])
        self.assertEqual(results, [(5, 10, None)])
        mock_pool.assert_not_called()

    def test_single_item_success(self):
        results = parallel_map(lambda x: f"out-{x}", ["a"])
        self.assertEqual(len(results), 1)
        item, value, exc = results[0]
        self.assertEqual(item, "a")
        self.assertEqual(value, "out-a")
        self.assertIsNone(exc)

    def test_single_item_exception_captured(self):
        # Same shape as parallel-path exception capture.
        boom = RuntimeError("boom")

        def raises(_):
            raise boom

        results = parallel_map(raises, ["x"])
        self.assertEqual(len(results), 1)
        item, value, exc = results[0]
        self.assertEqual(item, "x")
        self.assertIsNone(value)
        self.assertIs(exc, boom)


class TestParallelMapMultipleItems(unittest.TestCase):
    def test_all_items_invoked(self):
        seen: list[int] = []

        def record(x: int) -> int:
            seen.append(x)
            return x * 10

        items = [1, 2, 3, 4, 5]
        results = parallel_map(record, items)
        self.assertEqual(sorted(seen), items)
        self.assertEqual([r for (_, r, _) in results], [10, 20, 30, 40, 50])

    def test_results_in_input_order(self):
        items = [3, 1, 4, 1, 5, 9]
        results = parallel_map(lambda x: x * 2, items)
        self.assertEqual([item for (item, _, _) in results], items)
        self.assertEqual([r for (_, r, _) in results], [6, 2, 8, 2, 10, 18])


class TestParallelMapPreservesOrderUnderJitter(unittest.TestCase):
    """Spec § Faas 1: items=[3,1,4,1,5,9], fn=lambda x: time.sleep(random)*x —
    output items järjekord identne sisendiga (mitte completion order)."""

    def test_order_stable_when_completion_order_differs(self):
        random.seed(42)

        def slow(x: int) -> int:
            time.sleep(random.uniform(0.001, 0.02))
            return x

        items = [3, 1, 4, 1, 5, 9]
        results = parallel_map(slow, items, max_workers=8)
        self.assertEqual([item for (item, _, _) in results], items)
        self.assertEqual([r for (_, r, _) in results], items)


class TestParallelMapPartialFailure(unittest.TestCase):
    def test_one_item_raises_others_succeed(self):
        def maybe_fail(x: int) -> int:
            if x == 2:
                raise ValueError("boom")
            return x * 10

        results = parallel_map(maybe_fail, [1, 2, 3])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], (1, 10, None))
        item1, res1, exc1 = results[1]
        self.assertEqual(item1, 2)
        self.assertIsNone(res1)
        self.assertIsInstance(exc1, ValueError)
        self.assertEqual(str(exc1), "boom")
        self.assertEqual(results[2], (3, 30, None))

    def test_all_items_raise_helper_does_not_reraise(self):
        def always_fail(x: int) -> int:
            raise RuntimeError(f"fail-{x}")

        results = parallel_map(always_fail, [1, 2, 3])
        self.assertEqual(len(results), 3)
        for (item, res, exc), expected_item in zip(results, [1, 2, 3], strict=True):
            self.assertEqual(item, expected_item)
            self.assertIsNone(res)
            self.assertIsInstance(exc, RuntimeError)
            self.assertEqual(str(exc), f"fail-{expected_item}")


class TestParallelMapMaxWorkersOne(unittest.TestCase):
    """max_workers=1 should behave like sequential map (sanity check)."""

    def test_equivalent_to_sequential_map(self):
        items = [1, 2, 3, 4, 5]
        results = parallel_map(lambda x: x * x, items, max_workers=1)
        self.assertEqual(
            results,
            [(x, x * x, None) for x in items],
        )


class TestParallelMapPassesMaxWorkers(unittest.TestCase):
    """Verify max_workers kwarg reaches ThreadPoolExecutor."""

    def test_default_max_workers_is_eight(self):
        captured: dict = {}

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return ThreadPoolExecutor(*args, **kwargs)

        with patch("voog._concurrency.ThreadPoolExecutor", side_effect=spy):
            parallel_map(lambda x: x, [1, 2, 3])

        self.assertEqual(captured.get("max_workers"), 8)

    def test_custom_max_workers_forwarded(self):
        captured: dict = {}

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return ThreadPoolExecutor(*args, **kwargs)

        with patch("voog._concurrency.ThreadPoolExecutor", side_effect=spy):
            parallel_map(lambda x: x, [1, 2, 3], max_workers=3)

        self.assertEqual(captured.get("max_workers"), 3)


if __name__ == "__main__":
    unittest.main()
