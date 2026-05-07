"""Tests for voog.mcp.tools.pages."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from tests._test_helpers import _ann_get
from voog.mcp.tools import pages as pages_tools


class TestPagesTools(unittest.TestCase):
    def test_get_tools_returns_two(self):
        tools = pages_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["pages_list", "page_get"])

    def test_all_tools_have_full_read_only_triple(self):
        # Every tool here is read-only — assert the full explicit triple
        # (readOnlyHint=True, destructiveHint=False, idempotentHint=True).
        for tool in pages_tools.get_tools():
            ann = tool.annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"),
                True,
                f"{tool.name} must have readOnlyHint=True explicitly",
            )
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"),
                False,
                f"{tool.name} must have destructiveHint=False explicitly",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"),
                True,
                f"{tool.name} must have idempotentHint=True explicitly",
            )

    def test_pages_list_calls_client(self):
        client = MagicMock()
        client.get_all.return_value = [{"id": 1, "title": "Foo"}]
        result = pages_tools.call_tool("pages_list", {}, client)
        client.get_all.assert_called_once_with("/pages")
        # success_response with summary returns 2 TextContents (summary + JSON)
        self.assertEqual(len(result), 2)

    def test_page_get_calls_client(self):
        client = MagicMock()
        client.get.return_value = {"id": 42, "title": "Bar"}
        result = pages_tools.call_tool("page_get", {"page_id": 42}, client)
        client.get.assert_called_once_with("/pages/42")
        # No summary → 1 TextContent
        self.assertEqual(len(result), 1)

    def test_call_tool_unknown_name_returns_error(self):
        client = MagicMock()
        result = pages_tools.call_tool("nonexistent", {}, client)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_simplify_pages_projects_fields(self):
        from voog.projections import simplify_pages

        raw = [
            {
                "id": 1,
                "path": "foo",
                "title": "Foo",
                "hidden": False,
                "layout": {"id": 10, "title": "Default"},
                "content_type": "page",
                "parent_id": None,
                "language": {"code": "et"},
                "public_url": "https://example.com/foo",
            }
        ]
        out = simplify_pages(raw)
        self.assertEqual(out[0]["id"], 1)
        self.assertEqual(out[0]["layout_id"], 10)
        self.assertEqual(out[0]["layout_name"], "Default")
        self.assertEqual(out[0]["language_code"], "et")

    def test_simplify_pages_handles_missing_fields(self):
        from voog.projections import simplify_pages

        raw = [{"id": 2, "path": "x", "title": "X"}]  # no layout, no language
        out = simplify_pages(raw)
        self.assertEqual(out[0]["id"], 2)
        self.assertIsNone(out[0]["layout_id"])
        self.assertIsNone(out[0]["layout_name"])
        self.assertIsNone(out[0]["language_code"])

    def test_pages_list_error_returns_error_response(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = pages_tools.call_tool("pages_list", {}, client)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("pages_list failed", payload["error"])

    def test_page_get_error_returns_error_response(self):
        client = MagicMock()
        client.get.side_effect = Exception("boom")
        result = pages_tools.call_tool("page_get", {"page_id": 1}, client)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_pages_list_passes_q_language_code(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"language_code": "et"}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"q.page.language_code": "et"}
        )

    def test_pages_list_passes_q_content_type(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"content_type": "blog"}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"q.page.content_type": "blog"}
        )

    def test_pages_list_passes_q_node_id(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"node_id": 42}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"q.page.node_id": 42}
        )

    def test_pages_list_combines_multiple_q_filters(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool(
            "pages_list",
            {"language_code": "en", "content_type": "blog"},
            client,
        )
        client.get_all.assert_called_once_with(
            "/pages",
            params={
                "q.page.language_code": "en",
                "q.page.content_type": "blog",
            },
        )

    def test_pages_list_no_filters_passes_no_params(self):
        # Regression guard: behaviour with no filter args must match v1.2.x —
        # an unparameterised call to client.get_all("/pages").
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {}, client)
        client.get_all.assert_called_once_with("/pages")

    def test_pages_list_passes_path_prefix(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"path_prefix": "/blog"}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"path_prefix": "/blog"}
        )

    def test_pages_list_passes_search(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"search": "about"}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"search": "about"}
        )

    def test_pages_list_passes_parent_id(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"parent_id": 12}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"parent_id": 12}
        )

    def test_pages_list_passes_language_id(self):
        # language_id (integer) is the plain endpoint param. language_code
        # (string) is the q.page.language_code filter (covered in Task 1).
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool("pages_list", {"language_id": 627583}, client)
        client.get_all.assert_called_once_with(
            "/pages", params={"language_id": 627583}
        )

    def test_pages_list_passes_sort(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool(
            "pages_list", {"sort": "page.title.$asc"}, client
        )
        client.get_all.assert_called_once_with(
            "/pages", params={"s": "page.title.$asc"}
        )

    def test_pages_list_combines_q_and_plain_params(self):
        client = MagicMock()
        client.get_all.return_value = []
        pages_tools.call_tool(
            "pages_list",
            {"content_type": "blog", "search": "leather", "sort": "page.created_at.$desc"},
            client,
        )
        client.get_all.assert_called_once_with(
            "/pages",
            params={
                "q.page.content_type": "blog",
                "search": "leather",
                "s": "page.created_at.$desc",
            },
        )

    def test_pages_list_schema_exposes_q_filter_args(self):
        tool = next(t for t in pages_tools.get_tools() if t.name == "pages_list")
        props = tool.inputSchema["properties"]
        for arg in ("language_code", "content_type", "node_id"):
            self.assertIn(arg, props, f"pages_list schema missing {arg!r}")

    def test_pages_list_schema_exposes_plain_params(self):
        tool = next(t for t in pages_tools.get_tools() if t.name == "pages_list")
        props = tool.inputSchema["properties"]
        for arg in ("path_prefix", "search", "parent_id", "language_id", "sort"):
            self.assertIn(arg, props, f"pages_list schema missing {arg!r}")

    def test_pages_list_schema_filter_args_are_optional(self):
        tool = next(t for t in pages_tools.get_tools() if t.name == "pages_list")
        # Only `site` is required — every filter is optional.
        self.assertEqual(tool.inputSchema["required"], ["site"])

    def test_pages_list_schema_string_filters_have_minlength(self):
        # Empty-string filter values would silently pass through to Voog
        # (e.g. "?q.page.language_code=") — schema-level minLength:1
        # rejects this at the MCP boundary instead of letting Voog handle it.
        tool = next(t for t in pages_tools.get_tools() if t.name == "pages_list")
        props = tool.inputSchema["properties"]
        for arg in ("language_code", "content_type", "path_prefix", "search", "sort"):
            self.assertEqual(
                props[arg].get("minLength"),
                1,
                f"pages_list schema {arg!r} missing minLength:1 — empty strings would slip through",
            )


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import pages as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


if __name__ == "__main__":
    unittest.main()
