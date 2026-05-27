"""Tests for voog.mcp.tools.tags — tag CRUD (read + force-gated delete)."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import tags as tt


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in tt.get_tools())
        self.assertEqual(names, ["tag_delete", "tag_get", "tags_list"])


class TestTagsList(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in tt.get_tools()}
        self.assertIn("tags_list", names)

    def test_returns_full_tags(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "name": "leather", "slug": "leather", "taggings_count": 12},
            {"id": 2, "name": "tutorial", "slug": "tutorial", "taggings_count": 4},
        ]
        result = tt.call_tool("tags_list", {}, client)
        client.get_all.assert_called_once_with("/tags")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "leather")

    def test_annotations(self):
        tools = {t.name: t for t in tt.get_tools()}
        ann = tools["tags_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestTagGet(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in tt.get_tools()}
        self.assertIn("tag_get", names)

    def test_returns_full_tag(self):
        client = MagicMock()
        client.get.return_value = {
            "id": 5,
            "name": "leather",
            "slug": "leather",
            "taggings_count": 12,
        }
        result = tt.call_tool("tag_get", {"tag_id": 5}, client)
        client.get.assert_called_once_with("/tags/5")
        body = json.loads(result[0].text)
        self.assertEqual(body["name"], "leather")

    def test_requires_tag_id(self):
        client = MagicMock()
        result = tt.call_tool("tag_get", {}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_rejects_bool_tag_id(self):
        client = MagicMock()
        result = tt.call_tool("tag_get", {"tag_id": False}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)


class TestTagDelete(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in tt.get_tools()}
        self.assertIn("tag_delete", names)

    def test_requires_force(self):
        client = MagicMock()
        result = tt.call_tool("tag_delete", {"tag_id": 5}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        tt.call_tool("tag_delete", {"tag_id": 5, "force": True}, client)
        client.delete.assert_called_once_with("/tags/5")

    def test_requires_tag_id(self):
        client = MagicMock()
        result = tt.call_tool("tag_delete", {"force": True}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in tt.get_tools()}
        ann = tools["tag_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestServerToolRegistry(unittest.TestCase):
    def test_tags_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(tt, server.TOOL_GROUPS)
