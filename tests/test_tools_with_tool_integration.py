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

import unittest
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
)


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
