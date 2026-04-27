"""Tests for voog_mcp.tools.redirects."""
import asyncio
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._test_helpers import _ann_get
from voog_mcp.tools import redirects as redirects_tools


class TestRedirectsTools(unittest.TestCase):
    def test_get_tools_returns_two(self):
        tools = redirects_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["redirects_list", "redirect_add"])

    def test_redirects_list_full_annotation_triple(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        ann = tools["redirects_list"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_redirect_add_full_annotation_triple(self):
        # redirect_add is additive (creates a new rule), so destructiveHint=False.
        # NOT idempotent: repeat calls either create duplicate rules or error
        # on conflict — repeated calls have additional effect.
        tools = {t.name: t for t in redirects_tools.get_tools()}
        ann = tools["redirect_add"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)

    def test_redirect_add_schema_requires_source_and_destination(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        schema = tools["redirect_add"].inputSchema
        self.assertIn("source", schema["properties"])
        self.assertIn("destination", schema["properties"])
        self.assertIn("redirect_type", schema["properties"])
        # source + destination required, redirect_type optional (default 301)
        self.assertIn("source", schema["required"])
        self.assertIn("destination", schema["required"])
        self.assertNotIn("redirect_type", schema["required"])

    def test_redirects_list_calls_client(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "source": "/old", "destination": "/new", "redirect_type": 301},
        ]
        result = asyncio.run(redirects_tools.call_tool("redirects_list", {}, client))
        client.get_all.assert_called_once_with("/redirect_rules")
        # success_response with summary → 2 TextContents
        self.assertEqual(len(result), 2)

    def test_redirect_add_calls_client_with_defaults(self):
        client = MagicMock()
        client.post.return_value = {"id": 99, "source": "/a", "destination": "/b", "redirect_type": 301}
        result = asyncio.run(redirects_tools.call_tool(
            "redirect_add",
            {"source": "/a", "destination": "/b"},
            client,
        ))
        client.post.assert_called_once_with(
            "/redirect_rules",
            {
                "redirect_rule": {
                    "source": "/a",
                    "destination": "/b",
                    "redirect_type": 301,
                    "active": True,
                }
            },
        )
        self.assertEqual(len(result), 2)

    def test_redirect_add_passes_explicit_type(self):
        client = MagicMock()
        client.post.return_value = {"id": 100}
        asyncio.run(redirects_tools.call_tool(
            "redirect_add",
            {"source": "/x", "destination": "/y", "redirect_type": 410},
            client,
        ))
        client.post.assert_called_once_with(
            "/redirect_rules",
            {
                "redirect_rule": {
                    "source": "/x",
                    "destination": "/y",
                    "redirect_type": 410,
                    "active": True,
                }
            },
        )

    def test_redirect_add_rejects_invalid_type(self):
        client = MagicMock()
        result = asyncio.run(redirects_tools.call_tool(
            "redirect_add",
            {"source": "/x", "destination": "/y", "redirect_type": 999},
            client,
        ))
        client.post.assert_not_called()
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)

    def test_redirects_list_error_returns_error_response(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = asyncio.run(redirects_tools.call_tool("redirects_list", {}, client))
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)
        self.assertIn("redirects_list", payload["error"])

    def test_redirect_add_error_returns_error_response(self):
        client = MagicMock()
        client.post.side_effect = Exception("boom")
        result = asyncio.run(redirects_tools.call_tool(
            "redirect_add",
            {"source": "/a", "destination": "/b"},
            client,
        ))
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)
        self.assertIn("redirect_add", payload["error"])

    def test_call_tool_unknown_name_returns_error(self):
        client = MagicMock()
        result = asyncio.run(redirects_tools.call_tool("nonexistent", {}, client))
        payload = json.loads(result[0].text)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
