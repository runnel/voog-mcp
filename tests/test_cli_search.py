"""Tests for voog CLI: search subcommand."""

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from voog.cli.commands import search as search_cmd


class TestSearchCLI(unittest.TestCase):
    def _make_args(self, **overrides):
        defaults = {"q": "hello", "scope": "all", "language_code": None, "per_page": None}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_basic_search(self):
        client = MagicMock()
        client.get.return_value = [
            {"kind": "page", "id": 1, "title": "Hello", "path": "/hello"},
        ]
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = search_cmd.run(self._make_args(), client)
        self.assertEqual(rc, 0)
        self.assertIn("1 hits", out.getvalue())
        self.assertIn("Hello", out.getvalue())

    def test_scope_forwarded(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return []
            raise AssertionError(path)

        client.get.side_effect = dispatch
        with patch("sys.stdout", new_callable=StringIO):
            search_cmd.run(self._make_args(scope="articles"), client)
        first_call = client.get.call_args_list[0]
        self.assertEqual(first_call.kwargs["params"]["scope"], "articles")

    def test_zero_hits_print(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return []
            raise AssertionError(path)

        client.get.side_effect = dispatch
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = search_cmd.run(self._make_args(), client)
        self.assertEqual(rc, 0)
        self.assertIn("No hits", out.getvalue())

    def test_indexing_off_message(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return [{"id": 1, "title": "Real Page"}]
            raise AssertionError(path)

        client.get.side_effect = dispatch
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = search_cmd.run(self._make_args(), client)
        self.assertEqual(rc, 0)
        self.assertIn("indexing", out.getvalue().lower())

    def test_unexpected_shape_error(self):
        client = MagicMock()
        client.get.return_value = {"not": "a list"}
        with (
            patch("sys.stdout", new_callable=StringIO),
            patch("sys.stderr", new_callable=StringIO) as err,
        ):
            rc = search_cmd.run(self._make_args(), client)
        self.assertEqual(rc, 1)
        self.assertIn("unexpected", err.getvalue().lower())
