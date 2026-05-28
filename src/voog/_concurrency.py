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


def propagate_tool_context(client, fn: Callable[[T], R]) -> Callable[[T], R]:
    """Wrap ``fn`` so a ``ThreadPoolExecutor`` worker thread inherits the
    dispatching thread's ``client._local`` (tool_name + request_id) for
    the duration of one call.

    ``threading.local()`` is per-thread by design, so a naked
    ``parallel_map(client.get, items)`` call from inside a ``with
    client.with_tool(...)`` scope would emit worker HTTP requests with
    empty thread-local state — losing the ``X-MCP-Tool`` /
    ``X-Request-Id`` headers. This helper snapshots the parent's state
    at wrap time and re-sets it inside the worker before invoking ``fn``.

    Snapshot-at-wrap-time (not at-call-time) is intentional: the parent's
    ``with_tool`` scope might exit after dispatch but before the worker
    runs. parallel_map awaits all futures synchronously today so this is
    hypothetical, but the snapshot-then-yield contract is robust to a
    future async refactor.

    Single-item parallel_map runs synchronously on the calling thread —
    this wrapper still re-sets and clears ``_local``, which is a no-op
    when the parent state is already there (re-sets the same value).
    """
    parent_tool = getattr(client._local, "tool_name", None)
    parent_rid = getattr(client._local, "request_id", None)

    def _wrapped(item: T) -> R:
        if parent_tool:
            client._local.tool_name = parent_tool
        if parent_rid:
            client._local.request_id = parent_rid
        try:
            return fn(item)
        finally:
            # Clear so this worker thread doesn't carry stale state into
            # an unrelated future parallel_map run.
            client._local.tool_name = None
            client._local.request_id = None

    return _wrapped
