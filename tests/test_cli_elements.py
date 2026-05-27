"""Tests for voog element-move CLI subcommand.

Voog API uses QUERY-STRING params (not body) for /elements/{id}/move.
Verified against the Voog docs (link in the CLI module docstring).
"""

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from voog.cli.commands import elements as elements_cmd


class TestElementMoveCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {
            "element_id": 5,
            "page_id": None,
            "before": None,
            "after": None,
        }
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_page_id_only(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(page_id=99), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/elements/5/move", params={"page_id": 99})

    def test_before_only(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(before=3), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/elements/5/move", params={"before": 3})

    def test_after_only(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(after=7), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/elements/5/move", params={"after": 7})

    def test_page_id_and_before(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(page_id=1, before=8), client)
        self.assertEqual(rc, 0)
        sent = client.put.call_args.kwargs["params"]
        self.assertEqual(sent["page_id"], 1)
        self.assertEqual(sent["before"], 8)

    def test_before_and_after_mutually_exclusive(self):
        client = MagicMock()
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = elements_cmd.run(self._args(before=3, after=7), client)
        self.assertEqual(rc, 1)
        client.put.assert_not_called()
        self.assertIn("mutually exclusive", err.getvalue().lower())

    def test_no_field_errors(self):
        client = MagicMock()
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = elements_cmd.run(self._args(), client)
        self.assertEqual(rc, 1)
        client.put.assert_not_called()
        self.assertIn("supply", err.getvalue().lower())

    def test_api_error_propagates(self):
        client = MagicMock()
        client.put.side_effect = RuntimeError("422")
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = elements_cmd.run(self._args(before=1), client)
        self.assertEqual(rc, 1)
        self.assertIn("422", err.getvalue())
