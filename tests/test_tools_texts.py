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


class TestTextsBoolReject(unittest.TestCase):
    """T4b: require_int guards — bools must not reach Voog as ids."""

    def test_text_get_text_id_bool_rejected(self):
        client = MagicMock()
        result = texts_tools.call_tool("text_get", {"text_id": True}, client)
        self.assertTrue(result.isError)
        client.get.assert_not_called()

    def test_text_update_text_id_bool_rejected(self):
        client = MagicMock()
        result = texts_tools.call_tool(
            "text_update", {"text_id": False, "body": "<p>x</p>"}, client
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_page_add_content_page_id_bool_rejected(self):
        client = MagicMock()
        result = texts_tools.call_tool("page_add_content", {"page_id": True}, client)
        self.assertTrue(result.isError)
        client.get_all.assert_not_called()
        client.post.assert_not_called()


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
        client.get_all.return_value = []
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
        client.get_all.return_value = []
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

    def test_first_time_succeeds(self):
        # Regression: empty contents list (fresh page) → POST goes through.
        # Default behaviour is: GET /contents pre-check, then POST if no
        # area with the same name already exists.
        client = MagicMock()
        client.get_all.return_value = []
        client.post.return_value = {
            "id": 9999,
            "name": "body",
            "content_type": "text",
        }
        texts_tools.call_tool(
            "page_add_content",
            {"page_id": 5},
            client,
        )
        client.get_all.assert_called_once_with("/pages/5/contents")
        client.post.assert_called_once()
        path, body = client.post.call_args.args
        self.assertEqual(path, "/pages/5/contents")
        self.assertEqual(body["name"], "body")

    def test_rejects_duplicate_name_by_default(self):
        # Calling page_add_content twice with the same name was silently
        # creating two areas. Default behaviour now: pre-check GET; if a
        # content with the same name already exists, return an error_response
        # pointing the caller to text_update / force=true.
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 9999, "name": "body", "content_type": "text"},
        ]
        result = texts_tools.call_tool(
            "page_add_content",
            {"page_id": 5, "name": "body"},
            client,
        )
        client.get_all.assert_called_once_with("/pages/5/contents")
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        # Error message should hint at the right next step.
        payload = json.loads(result.content[0].text)
        self.assertIn("text_update", payload["error"])
        self.assertIn("force=true", payload["error"])

    def test_force_skips_dup_check(self):
        # force=true skips the GET pre-check and POSTs blindly. This is the
        # escape hatch for legitimate repeated-name use cases (e.g. a page
        # template with multiple unnamed/'body' content areas).
        client = MagicMock()
        client.post.return_value = {
            "id": 10000,
            "name": "body",
            "content_type": "text",
        }
        texts_tools.call_tool(
            "page_add_content",
            {"page_id": 5, "name": "body", "force": True},
            client,
        )
        client.get_all.assert_not_called()
        client.post.assert_called_once()
