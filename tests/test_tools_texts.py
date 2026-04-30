"""Tests for voog.mcp.tools.texts — text content + page content area tools."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import texts as texts_tools


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in texts_tools.get_tools())
        self.assertEqual(
            names,
            ["page_add_content", "text_get", "text_update"],
        )


class TestTextGet(unittest.TestCase):
    def test_get_returns_full_text_object(self):
        client = MagicMock()
        client.get.return_value = {"id": 7, "body": "<p>x</p>"}
        result = texts_tools.call_tool("text_get", {"text_id": 7}, client)
        client.get.assert_called_once_with("/texts/7")
        body = json.loads(result[0].text)
        self.assertEqual(body["body"], "<p>x</p>")


class TestTextUpdate(unittest.TestCase):
    def test_put_text_body(self):
        client = MagicMock()
        client.put.return_value = {"id": 7, "body": "<p>updated</p>"}
        texts_tools.call_tool(
            "text_update",
            {"text_id": 7, "body": "<p>updated</p>"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/texts/7")
        self.assertEqual(body, {"body": "<p>updated</p>"})

    def test_rejects_missing_body(self):
        client = MagicMock()
        result = texts_tools.call_tool("text_update", {"text_id": 7}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestPageAddContent(unittest.TestCase):
    def test_default_body_text(self):
        # Per skill: fresh page returns [] from /contents until edit-mode
        # opens it. POST /pages/{id}/contents materialises the area.
        client = MagicMock()
        client.post.return_value = {
            "id": 9999,
            "name": "body",
            "content_type": "text",
            "text": {"id": 88},
        }
        texts_tools.call_tool(
            "page_add_content",
            {"page_id": 5},
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/pages/5/contents")
        self.assertEqual(body["name"], "body")
        self.assertEqual(body["content_type"], "text")

    def test_named_gallery_area(self):
        client = MagicMock()
        client.post.return_value = {
            "id": 9999,
            "name": "gallery_1",
            "content_type": "gallery",
        }
        texts_tools.call_tool(
            "page_add_content",
            {
                "page_id": 5,
                "name": "gallery_1",
                "content_type": "gallery",
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["name"], "gallery_1")
        self.assertEqual(body["content_type"], "gallery")
