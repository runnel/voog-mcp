"""Tests for voog articles comments CLI subcommands."""

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from voog.cli.commands import articles as articles_cmd


class TestCommentsListCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"article_id": 7}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_lists_comments(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 11, "author": "Alice", "body": "Hi", "is_spam": False},
        ]
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = articles_cmd.cmd_comments_list(self._args(), client)
        self.assertEqual(rc, 0)
        client.get_all.assert_called_once_with("/articles/7/comments")
        self.assertIn("Alice", out.getvalue())

    def test_empty_comments(self):
        client = MagicMock()
        client.get_all.return_value = []
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = articles_cmd.cmd_comments_list(self._args(), client)
        self.assertEqual(rc, 0)
        self.assertIn("No comments", out.getvalue())

    def test_error_path(self):
        client = MagicMock()
        client.get_all.side_effect = RuntimeError("boom")
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = articles_cmd.cmd_comments_list(self._args(), client)
        self.assertEqual(rc, 1)
        self.assertIn("boom", err.getvalue())


class TestCommentsDeleteCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"article_id": 7, "comment_id": 11, "force": False}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_force_skips_confirm(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = articles_cmd.cmd_comments_delete(self._args(force=True), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once_with("/articles/7/comments/11")

    def test_confirm_yes_deletes(self):
        client = MagicMock()
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout", new_callable=StringIO):
                rc = articles_cmd.cmd_comments_delete(self._args(), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once_with("/articles/7/comments/11")

    def test_confirm_no_aborts(self):
        client = MagicMock()
        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", new_callable=StringIO):
                rc = articles_cmd.cmd_comments_delete(self._args(), client)
        self.assertEqual(rc, 0)
        client.delete.assert_not_called()


class TestCommentsToggleSpamCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"article_id": 7, "comment_id": 11, "is_spam": "true"}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_mark_spam(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = articles_cmd.cmd_comments_toggle_spam(self._args(), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with(
            "/articles/7/comments/11",
            {"is_spam": True},
        )

    def test_unmark_spam(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = articles_cmd.cmd_comments_toggle_spam(
                self._args(is_spam="false"),
                client,
            )
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with(
            "/articles/7/comments/11",
            {"is_spam": False},
        )
