"""Tests for voog.mcp.tools.multilingual — languages, nodes."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import multilingual as mt


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in mt.get_tools())
        self.assertEqual(names, ["languages_list", "node_get", "nodes_list"])


class TestLanguagesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 627583,
                "code": "et",
                "title": "Eesti",
                "default_language": True,
                "published": True,
                "position": 1,
            },
            {
                "id": 627582,
                "code": "en",
                "title": "English",
                "default_language": False,
                "published": True,
                "position": 2,
            },
        ]
        result = mt.call_tool("languages_list", {}, client)
        client.get_all.assert_called_once_with("/languages")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["code"], "et")
        self.assertIs(items[0]["default_language"], True)


class TestNodesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Home", "parent_id": None, "position": 1},
            {"id": 2, "title": "Sub", "parent_id": 1, "position": 1},
        ]
        result = mt.call_tool("nodes_list", {}, client)
        client.get_all.assert_called_once_with("/nodes")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 2)


class TestNodeGet(unittest.TestCase):
    def test_returns_node(self):
        client = MagicMock()
        client.get.return_value = {
            "id": 5,
            "title": "Pood",
            "pages": [
                {"id": 100, "language_id": 627583},
                {"id": 101, "language_id": 627582},
            ],
        }
        result = mt.call_tool("node_get", {"node_id": 5}, client)
        client.get.assert_called_once_with("/nodes/5")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 5)
        self.assertEqual(len(body["pages"]), 2)
