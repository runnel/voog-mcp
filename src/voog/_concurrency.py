"""Tool-level concurrency helper.

Tools dispatch as sync functions wrapped in ``asyncio.to_thread`` (see
``server.handle_call_tool``), so each tool call already runs in its own
outer thread. To parallelize the I/O *inside* a tool, this module spawns
a short-lived ``ThreadPoolExecutor`` — urllib HTTP calls run concurrently
in worker threads, the outer thread blocks on shutdown, and the MCP event
loop stays free.

Sync stays sync: the spec deliberately rejected an async refactor (PR #44
contract), so this helper is the parallelization primitive shared by all
tools that fan out to multiple HTTP requests.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    fn: Callable[[T], R],
    items: list[T],
    *,
    max_workers: int = 8,
) -> list[tuple[T, R | None, Exception | None]]:
    """Run ``fn(item)`` in parallel across ``items``, returning per-item results.

    Returns ``[(item, result, exception), ...]`` in the original input order
    (not completion order), so callers can pair input/output deterministically
    without bookkeeping. Distinguish success from failure by checking whether
    ``exception`` is not None — if ``fn`` returns ``None`` on success, the tuple
    is ``(item, None, None)``, which is success, not a failure with a missing
    exception. Caller decides what to do with errors — this helper never raises.

    Empty ``items`` returns ``[]`` without spawning a pool. Single-item
    lists run synchronously, also without a pool — same output shape, no
    thread-pool startup/teardown overhead. In the synchronous path ``fn``
    runs on the *calling* thread, not a worker thread; callers inspecting
    ``threading.current_thread()``, writing thread-locals, or relying on
    the executor's KeyboardInterrupt handling will observe a behavior
    delta. None of the in-tree callers do.
    """
    if not items:
        return []
    if len(items) == 1:
        item = items[0]
        try:
            return [(item, fn(item), None)]
        except Exception as e:
            return [(item, None, e)]
    results: list[tuple[T, R | None, Exception | None]] = [None] * len(items)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fn, item): idx for idx, item in enumerate(items)}
        for future in as_completed(futures):
            idx = futures[future]
            item = items[idx]
            try:
                results[idx] = (item, future.result(), None)
            except Exception as e:
                results[idx] = (item, None, e)
    return results
