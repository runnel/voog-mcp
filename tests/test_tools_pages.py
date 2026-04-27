"""Tests for voog_mcp.tools.pages."""
import asyncio
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.tools import pages as pages_tools


class TestPagesTools(unittest.TestCase):
    def test_get_tools_returns_three(self):
        tools = pages_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["pages_list", "page_get", "pages_pull"])
        # All marked read-only — annotation may be dict or model object
        for t in tools:
            ann = t.annotations
            if hasattr(ann, "read_only_hint"):
                self.assertTrue(ann.read_only_hint)
            elif hasattr(ann, "readOnlyHint"):
                self.assertTrue(ann.readOnlyHint)
            elif isinstance(ann, dict):
                # allow either readOnlyHint or read_only_hint
                self.assertTrue(ann.get("readOnlyHint") or ann.get("read_only_hint"))
            else:
                self.fail(f"Unexpected annotations type: {type(ann)}")

    def test_pages_list_calls_client(self):
        client = MagicMock()
        client.get_all.return_value = [{"id": 1, "title": "Foo"}]
        result = asyncio.run(pages_tools.call_tool("pages_list", {}, client))
        client.get_all.assert_called_once_with("/pages")
        # success_response with summary returns 2 TextContents (summary + JSON)
        self.assertEqual(len(result), 2)

    def test_page_get_calls_client(self):
        client = MagicMock()
        client.get.return_value = {"id": 42, "title": "Bar"}
        result = asyncio.run(pages_tools.call_tool("page_get", {"page_id": 42}, client))
        client.get.assert_called_once_with("/pages/42")
        # No summary → 1 TextContent
        self.assertEqual(len(result), 1)

    def test_pages_pull_calls_get_all(self):
        client = MagicMock()
        client.get_all.return_value = []
        result = asyncio.run(pages_tools.call_tool("pages_pull", {}, client))
        client.get_all.assert_called_once_with("/pages")
        # Even with empty list, success_response with summary returns 2 TextContents
        self.assertEqual(len(result), 2)

    def test_call_tool_unknown_name_returns_error(self):
        client = MagicMock()
        result = asyncio.run(pages_tools.call_tool("nonexistent", {}, client))
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)

    def test_simplify_pages_projects_fields(self):
        raw = [{
            "id": 1, "path": "foo", "title": "Foo", "hidden": False,
            "layout": {"id": 10, "title": "Default"},
            "content_type": "page",
            "parent_id": None,
            "language": {"code": "et"},
            "public_url": "https://runnel.ee/foo",
        }]
        out = pages_tools._simplify_pages(raw)
        self.assertEqual(out[0]["id"], 1)
        self.assertEqual(out[0]["layout_id"], 10)
        self.assertEqual(out[0]["layout_name"], "Default")
        self.assertEqual(out[0]["language_code"], "et")

    def test_simplify_pages_handles_missing_fields(self):
        raw = [{"id": 2, "path": "x", "title": "X"}]  # no layout, no language
        out = pages_tools._simplify_pages(raw)
        self.assertEqual(out[0]["id"], 2)
        self.assertIsNone(out[0]["layout_id"])
        self.assertIsNone(out[0]["layout_name"])
        self.assertIsNone(out[0]["language_code"])

    def test_pages_list_error_returns_error_response(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = asyncio.run(pages_tools.call_tool("pages_list", {}, client))
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)
        self.assertIn("pages_list ebaõnnestus", payload["error"])

    def test_page_get_error_returns_error_response(self):
        client = MagicMock()
        client.get.side_effect = Exception("boom")
        result = asyncio.run(pages_tools.call_tool("page_get", {"page_id": 1}, client))
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
