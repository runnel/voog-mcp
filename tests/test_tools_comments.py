"""Tests for voog.mcp.tools.comments — article comments moderation."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import comments as ct


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in ct.get_tools())
        self.assertEqual(
            names,
            ["comment_delete", "comment_toggle_spam", "comments_list"],
        )


class TestCommentsList(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in ct.get_tools()}
        self.assertIn("comments_list", names)

    def test_returns_full_comments(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 11,
                "author": "Alice",
                "body": "Nice post",
                "email": "alice@example.com",
                "is_spam": False,
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        result = ct.call_tool("comments_list", {"article_id": 7}, client)
        client.get_all.assert_called_once_with("/articles/7/comments")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["author"], "Alice")

    def test_requires_article_id(self):
        client = MagicMock()
        result = ct.call_tool("comments_list", {}, client)
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)

    def test_rejects_bool_article_id(self):
        client = MagicMock()
        result = ct.call_tool("comments_list", {"article_id": True}, client)
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in ct.get_tools()}
        ann = tools["comments_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestCommentDelete(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in ct.get_tools()}
        self.assertIn("comment_delete", names)

    def test_requires_force(self):
        client = MagicMock()
        result = ct.call_tool(
            "comment_delete",
            {"article_id": 7, "comment_id": 11},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        ct.call_tool(
            "comment_delete",
            {"article_id": 7, "comment_id": 11, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/articles/7/comments/11")

    def test_requires_article_id(self):
        client = MagicMock()
        result = ct.call_tool(
            "comment_delete",
            {"comment_id": 11, "force": True},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_comment_id(self):
        client = MagicMock()
        result = ct.call_tool(
            "comment_delete",
            {"article_id": 7, "force": True},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in ct.get_tools()}
        ann = tools["comment_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestCommentToggleSpam(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in ct.get_tools()}
        self.assertIn("comment_toggle_spam", names)

    def test_mark_spam(self):
        client = MagicMock()
        client.put.return_value = {"id": 11, "is_spam": True}
        ct.call_tool(
            "comment_toggle_spam",
            {"article_id": 7, "comment_id": 11, "is_spam": True},
            client,
        )
        client.put.assert_called_once_with(
            "/articles/7/comments/11",
            {"is_spam": True},
        )

    def test_unmark_spam(self):
        client = MagicMock()
        client.put.return_value = {"id": 11, "is_spam": False}
        ct.call_tool(
            "comment_toggle_spam",
            {"article_id": 7, "comment_id": 11, "is_spam": False},
            client,
        )
        client.put.assert_called_once_with(
            "/articles/7/comments/11",
            {"is_spam": False},
        )

    def test_no_envelope_wrapper(self):
        client = MagicMock()
        client.put.return_value = {"id": 1}
        ct.call_tool(
            "comment_toggle_spam",
            {"article_id": 7, "comment_id": 1, "is_spam": True},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("comment", sent_body)
        self.assertIn("is_spam", sent_body)

    def test_requires_is_spam_bool(self):
        client = MagicMock()
        result = ct.call_tool(
            "comment_toggle_spam",
            {"article_id": 7, "comment_id": 11},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_rejects_string_is_spam(self):
        client = MagicMock()
        result = ct.call_tool(
            "comment_toggle_spam",
            {"article_id": 7, "comment_id": 11, "is_spam": "true"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in ct.get_tools()}
        ann = tools["comment_toggle_spam"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestServerToolRegistry(unittest.TestCase):
    def test_comments_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(ct, server.TOOL_GROUPS)
