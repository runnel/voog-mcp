"""Tests for voog_mcp.server dispatch wiring (review fix #3).

The point: confirm that ``handle_call_tool`` / ``handle_read_resource`` invoke
the sync group functions via ``asyncio.to_thread`` so blocking urllib I/O does
not stall the MCP event loop. We do that by:

  - asserting the group functions are plain sync ``def`` (not coroutines), so
    ``await group.call_tool(...)`` would have been a runtime ``TypeError``,
  - patching ``asyncio.to_thread`` and running the registered handlers to
    verify the sync function is genuinely off-loaded to a worker thread.

Handlers live inside ``run_server()`` as decorated closures, so we exercise
them by mocking the ``Server`` class to capture the decorator targets and
short-circuit ``stdio_server`` before it touches real stdin/stdout.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import unittest
from unittest.mock import MagicMock, patch

from voog.config import GlobalConfig, SiteConfig
from voog.mcp import server as server_module
from voog.mcp.resources import (
    articles as articles_resources,
)
from voog.mcp.resources import (
    layouts as layouts_resources,
)
from voog.mcp.resources import (
    pages as pages_resources,
)
from voog.mcp.resources import (
    products as products_resources,
)
from voog.mcp.resources import (
    redirects as redirects_resources,
)
from voog.mcp.tools import (
    layouts as layouts_tools,
)
from voog.mcp.tools import (
    layouts_sync as layouts_sync_tools,
)
from voog.mcp.tools import (
    pages as pages_tools,
)
from voog.mcp.tools import (
    pages_mutate as pages_mutate_tools,
)
from voog.mcp.tools import (
    products as products_tools,
)
from voog.mcp.tools import (
    products_images as products_images_tools,
)
from voog.mcp.tools import (
    redirects as redirects_tools,
)
from voog.mcp.tools import (
    snapshot as snapshot_tools,
)


class TestGroupFunctionsAreSync(unittest.TestCase):
    """Each tool/resource group exposes a plain sync ``def`` for dispatch.

    If anyone reverts one back to ``async def`` the dispatch in server.py
    (``await asyncio.to_thread(group.call_tool, ...)``) would receive a
    coroutine and break in confusing ways at runtime; this test catches that
    regression statically.
    """

    def test_all_tool_groups_have_sync_call_tool(self):
        groups = [
            layouts_tools,
            layouts_sync_tools,
            pages_tools,
            pages_mutate_tools,
            products_tools,
            products_images_tools,
            redirects_tools,
            snapshot_tools,
        ]
        for group in groups:
            with self.subTest(group=group.__name__):
                self.assertFalse(
                    inspect.iscoroutinefunction(group.call_tool),
                    f"{group.__name__}.call_tool must be sync def "
                    "(server dispatches via asyncio.to_thread).",
                )

    def test_all_resource_groups_have_sync_read_resource(self):
        groups = [
            articles_resources,
            layouts_resources,
            pages_resources,
            products_resources,
            redirects_resources,
        ]
        for group in groups:
            with self.subTest(group=group.__name__):
                self.assertFalse(
                    inspect.iscoroutinefunction(group.read_resource),
                    f"{group.__name__}.read_resource must be sync def "
                    "(server dispatches via asyncio.to_thread).",
                )


def _capture_handlers():
    """Run ``run_server`` far enough to capture the registered handlers.

    Mocks out ``Server`` so its ``call_tool``/``read_resource`` decorators
    grab the wrapped function for inspection, and short-circuits
    ``stdio_server`` so we never block on real stdin.

    Uses the new multi-site API: builds a real ``GlobalConfig`` with a
    single ``test`` site and passes it (plus an env dict) to ``run_server``.
    ``VoogClient`` is patched so no network calls are made.
    """
    captured: dict = {}

    fake_server = MagicMock()

    def _decorator_factory(slot: str):
        def _decorator():
            def _wrap(fn):
                captured[slot] = fn
                return fn

            return _wrap

        return _decorator

    fake_server.list_tools = _decorator_factory("list_tools")
    fake_server.call_tool = _decorator_factory("call_tool")
    fake_server.list_resources = _decorator_factory("list_resources")
    fake_server.read_resource = _decorator_factory("read_resource")

    class _StdioCancel:
        async def __aenter__(self):
            raise asyncio.CancelledError("stop here — handlers already captured")

        async def __aexit__(self, *exc):
            return False

    # Build a minimal GlobalConfig with one site named "test".
    global_cfg = GlobalConfig(
        sites={"test": SiteConfig(name="test", host="example.com", api_key_env="TEST_API_TOKEN")},
        default_site="test",
    )
    env = {"TEST_API_TOKEN": "dummy-token"}

    with (
        patch.object(server_module, "Server", return_value=fake_server),
        patch.object(server_module, "VoogClient") as mock_client_cls,
        patch.object(server_module, "stdio_server", return_value=_StdioCancel()),
    ):
        mock_client_cls.return_value = MagicMock()
        try:
            asyncio.run(server_module.run_server(global_cfg, env))
        except asyncio.CancelledError:
            pass
        return captured, mock_client_cls.return_value


class TestServerDispatchesViaToThread(unittest.TestCase):
    """``handle_call_tool`` and ``handle_read_resource`` must offload work."""

    def test_handle_call_tool_uses_asyncio_to_thread(self):
        handlers, fake_client = _capture_handlers()
        handle_call_tool = handlers["call_tool"]

        captured_thread: dict = {}

        def fake_call_tool(name, arguments, client):
            captured_thread["thread"] = threading.current_thread()
            return ["ok"]

        # Replace one real group's call_tool with a tracker so we observe
        # the dispatch path end-to-end (handler → asyncio.to_thread → group).
        with (
            patch.object(redirects_tools, "call_tool", side_effect=fake_call_tool) as group_call,
            patch.object(server_module.asyncio, "to_thread", wraps=asyncio.to_thread) as to_thread,
        ):
            result = asyncio.run(handle_call_tool("redirects_list", {"site": "test"}))

        self.assertEqual(result, ["ok"])
        # asyncio.to_thread must have been used (not a plain await on a coroutine)
        to_thread.assert_called_once()
        # The first positional argument is the sync callable, not a coroutine
        passed_callable = to_thread.call_args.args[0]
        self.assertFalse(inspect.iscoroutine(passed_callable))
        # And the sync function actually executed off the main thread
        self.assertIsNotNone(captured_thread.get("thread"))
        self.assertIsNot(captured_thread["thread"], threading.main_thread())
        group_call.assert_called_once()

    def test_handle_read_resource_uses_asyncio_to_thread(self):
        handlers, fake_client = _capture_handlers()
        handle_read_resource = handlers["read_resource"]

        captured_thread: dict = {}

        def fake_read_resource(uri, client):
            captured_thread["thread"] = threading.current_thread()
            return ["ok"]

        with (
            patch.object(redirects_resources, "read_resource", side_effect=fake_read_resource),
            patch.object(server_module.asyncio, "to_thread", wraps=asyncio.to_thread) as to_thread,
        ):
            result = asyncio.run(handle_read_resource("voog://test/redirects"))

        self.assertEqual(result, ["ok"])
        to_thread.assert_called_once()
        self.assertIsNotNone(captured_thread.get("thread"))
        self.assertIsNot(captured_thread["thread"], threading.main_thread())


if __name__ == "__main__":
    unittest.main()
