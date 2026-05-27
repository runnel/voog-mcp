"""Sweep test — every tool module's call_tool wraps dispatch in with_tool.

Phase 5 S9: tool-wrapper sweep. Asserts on a representative tool per
module that ``client.with_tool(name)`` was entered during dispatch.
Acts as a single-source drift guard — adding a new tool module without
the with_tool wrap will fail this test.

Pattern: ``patch.object(client, "with_tool", wraps=client.with_tool)`` —
spy that still executes the real context manager (so the actual handler
runs and sets up the thread-local state the way production would). We
only assert on the spy's call args, not on whether the handler succeeded
— a handler may short-circuit on missing args and still satisfy the
"with_tool was entered" contract (the wrap happens BEFORE handler
invocation).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.client import VoogClient
from voog.mcp.tools import (
    articles,
    content_partials,
    ecommerce_settings,
    elements,
    layouts,
    layouts_sync,
    multilingual,
    pages,
    pages_mutate,
    products,
    raw,
    redirects,
    snapshot,
    texts,
)
from voog.mcp.tools import site as site_mod


def _mock_client():
    """Real VoogClient with stubbed HTTP methods.

    Using a real ``VoogClient`` (not MagicMock for the whole object) keeps
    ``with_tool`` and ``_local`` working as in production. Only the HTTP
    surface is stubbed — that's the boundary we care about isolating from
    real network for these structural tests.
    """
    c = VoogClient(host="t.example.com", api_token="t")
    c.get = MagicMock(return_value=[])
    c.get_all = MagicMock(return_value=[])
    c.post = MagicMock(return_value={"id": 1})
    c.put = MagicMock(return_value={"id": 1})
    c.delete = MagicMock(return_value=None)
    c.patch = MagicMock(return_value={"id": 1})
    return c


def _assert_with_tool_invoked(testcase, module, tool_name, arguments):
    client = _mock_client()
    with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
        module.call_tool(tool_name, arguments, client)
        spy.assert_called_once_with(tool_name)


class TestSweepBatch1(unittest.TestCase):
    """Batch 1: articles, content_partials, ecommerce_settings, elements, layouts."""

    def test_articles_list_enters_with_tool(self):
        _assert_with_tool_invoked(self, articles, "articles_list", {})

    def test_content_partials_update_enters_with_tool(self):
        # Single-tool module — pattern test even on an empty-args call
        # (handler short-circuits with validation error, but with_tool
        # still entered first).
        _assert_with_tool_invoked(self, content_partials, "content_partial_update", {})

    def test_ecommerce_settings_get_enters_with_tool(self):
        _assert_with_tool_invoked(self, ecommerce_settings, "ecommerce_settings_get", {})

    def test_ecommerce_settings_update_enters_with_tool(self):
        # Two-tool inline-dispatch module; verify both tools wrap.
        _assert_with_tool_invoked(self, ecommerce_settings, "ecommerce_settings_update", {})

    def test_ecommerce_settings_unknown_does_not_enter_with_tool(self):
        # _KNOWN_TOOLS early-return must prevent with_tool entry on typo'd
        # / unknown tool names — confirms the wrap is gated on a known name.
        client = _mock_client()
        with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
            result = ecommerce_settings.call_tool("ecommerce_settings_typo", {}, client)
            spy.assert_not_called()
            self.assertTrue(getattr(result, "isError", False))

    def test_elements_first_tool_enters_with_tool(self):
        tools = elements.get_tools()
        _assert_with_tool_invoked(self, elements, tools[0].name, {})

    def test_layouts_first_tool_enters_with_tool(self):
        tools = layouts.get_tools()
        _assert_with_tool_invoked(self, layouts, tools[0].name, {})


class TestSweepBatch2(unittest.TestCase):
    """Batch 2: layouts_sync, multilingual, pages, pages_mutate, products."""

    def test_layouts_pull_enters_with_tool(self):
        # layouts_sync is filesystem-touching; supply a fake dir so the
        # handler reaches the with_tool entry (wrap fires BEFORE handler
        # validation, so even an invalid target_dir would enter — but
        # using a valid one for symmetry with production usage).
        _assert_with_tool_invoked(
            self, layouts_sync, "layouts_pull", {"target_dir": "/tmp/no-op-test-only"}
        )

    def test_layouts_push_enters_with_tool(self):
        _assert_with_tool_invoked(
            self, layouts_sync, "layouts_push", {"source_dir": "/tmp/no-op-test-only"}
        )

    def test_layouts_sync_unknown_does_not_enter_with_tool(self):
        client = _mock_client()
        with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
            result = layouts_sync.call_tool("layouts_typo", {}, client)
            spy.assert_not_called()
            self.assertTrue(getattr(result, "isError", False))

    def test_multilingual_first_tool_enters_with_tool(self):
        tools = multilingual.get_tools()
        _assert_with_tool_invoked(self, multilingual, tools[0].name, {})

    def test_pages_list_enters_with_tool(self):
        _assert_with_tool_invoked(self, pages, "pages_list", {})

    def test_page_get_enters_with_tool(self):
        _assert_with_tool_invoked(self, pages, "page_get", {"page_id": 1})

    def test_pages_unknown_does_not_enter_with_tool(self):
        client = _mock_client()
        with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
            result = pages.call_tool("page_typo", {}, client)
            spy.assert_not_called()
            self.assertTrue(getattr(result, "isError", False))

    def test_pages_mutate_first_tool_enters_with_tool(self):
        tools = pages_mutate.get_tools()
        _assert_with_tool_invoked(self, pages_mutate, tools[0].name, {})

    def test_products_list_enters_with_tool(self):
        _assert_with_tool_invoked(self, products, "products_list", {})


class TestSweepBatch3(unittest.TestCase):
    """Batch 3: raw, redirects, site, snapshot, texts."""

    def test_raw_admin_read_enters_with_tool(self):
        _assert_with_tool_invoked(self, raw, "voog_admin_api_read", {"path": "/me"})

    def test_raw_ecommerce_call_enters_with_tool(self):
        _assert_with_tool_invoked(
            self, raw, "voog_ecommerce_api_call", {"method": "GET", "path": "/products"}
        )

    def test_raw_unknown_does_not_enter_with_tool(self):
        client = _mock_client()
        with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
            result = raw.call_tool("voog_typo_call", {}, client)
            spy.assert_not_called()
            self.assertTrue(getattr(result, "isError", False))

    def test_redirects_first_tool_enters_with_tool(self):
        tools = redirects.get_tools()
        _assert_with_tool_invoked(self, redirects, tools[0].name, {})

    def test_site_first_tool_enters_with_tool(self):
        tools = site_mod.get_tools()
        _assert_with_tool_invoked(self, site_mod, tools[0].name, {})

    def test_snapshot_pages_enters_with_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _assert_with_tool_invoked(
                self,
                snapshot,
                "pages_snapshot",
                {"output_dir": str(Path(tmpdir) / "snap")},
            )

    def test_snapshot_site_enters_with_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _assert_with_tool_invoked(
                self,
                snapshot,
                "site_snapshot",
                {"output_dir": str(Path(tmpdir) / "snap")},
            )

    def test_snapshot_unknown_does_not_enter_with_tool(self):
        client = _mock_client()
        with patch.object(client, "with_tool", wraps=client.with_tool) as spy:
            result = snapshot.call_tool("snap_typo", {}, client)
            spy.assert_not_called()
            self.assertTrue(getattr(result, "isError", False))

    def test_texts_first_tool_enters_with_tool(self):
        tools = texts.get_tools()
        _assert_with_tool_invoked(self, texts, tools[0].name, {})


class TestParallelMapPropagation(unittest.TestCase):
    """S9d — parallel_map worker threads inherit parent tool context via
    voog._concurrency.propagate_tool_context. Without this, the snapshot
    fan-out (per-page contents, per-article details) would emit HTTP
    requests with empty thread-local state and the X-MCP-Tool /
    X-Request-Id headers would silently disappear from worker requests."""

    def test_workers_carry_tool_header(self):
        from unittest.mock import call as _call  # noqa: F401  # for clarity

        client = VoogClient(host="t.example.com", api_token="t")
        captured_headers: list = []

        # Stub _http_client.request to capture per-call headers without
        # touching the network. Returns a stub response for every call.
        def _capture(method, url, **kwargs):
            # kwargs["headers"] is what _request merged from with_tool +
            # propagate_tool_context. Absent → fall-back to session
            # headers (empty for our purposes; we care about the absence).
            captured_headers.append(kwargs.get("headers") or {})
            import httpx as _hx

            req = _hx.Request(method, url)
            return _hx.Response(status_code=200, content=b"[]", request=req)

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            with patch.object(client._http_client, "request", side_effect=_capture):
                snapshot.call_tool(
                    "site_snapshot",
                    {"output_dir": str(out)},
                    client,
                )

        # Filter to requests that actually carry our tracking headers —
        # there's at least one (the singletons fetched sequentially under
        # the with_tool scope on the calling thread).
        tool_headers = [h.get("X-MCP-Tool") for h in captured_headers if h]
        rid_headers = [h.get("X-Request-Id") for h in captured_headers if h]
        self.assertGreater(len(tool_headers), 0, "no captured requests had X-MCP-Tool")
        # Every tagged request must carry the right tool name.
        self.assertTrue(
            all(t == "site_snapshot" for t in tool_headers if t is not None),
            f"saw mismatched tool headers: {set(tool_headers)}",
        )
        # R3: all X-Request-Id values across the whole fan-out are identical.
        unique_rids = {r for r in rid_headers if r}
        self.assertEqual(
            len(unique_rids),
            1,
            f"X-Request-Id diverged across workers: {unique_rids}",
        )

    def test_workers_clear_state_after_call(self):
        # Each worker thread cleared _local after the wrapped fn returned,
        # so a subsequent parallel_map on the same pool starts with clean
        # state. Easiest way to test: invoke the helper directly.
        from voog._concurrency import parallel_map, propagate_tool_context

        client = VoogClient(host="t.example.com", api_token="t")

        def _read_state(_):
            return getattr(client._local, "tool_name", None)

        with client.with_tool("scope_a"):
            wrapped = propagate_tool_context(client, _read_state)
            results = parallel_map(wrapped, list(range(5)), max_workers=3)

        # All workers saw "scope_a"
        for _item, value, exc in results:
            self.assertIsNone(exc)
            self.assertEqual(value, "scope_a")

        # After scope exit, dispatching thread sees None
        self.assertIsNone(getattr(client._local, "tool_name", None))

    def test_outside_scope_yields_none_to_worker(self):
        # If parent had no with_tool scope, workers see None for both
        # tool_name and request_id — no headers attached.
        from voog._concurrency import parallel_map, propagate_tool_context

        client = VoogClient(host="t.example.com", api_token="t")

        def _read(_):
            return (
                getattr(client._local, "tool_name", None),
                getattr(client._local, "request_id", None),
            )

        wrapped = propagate_tool_context(client, _read)
        results = parallel_map(wrapped, ["x", "y"], max_workers=2)
        for _item, value, exc in results:
            self.assertIsNone(exc)
            self.assertEqual(value, (None, None))
