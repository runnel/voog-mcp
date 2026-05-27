"""Tests for voog element-move CLI subcommand."""

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from voog.cli.commands import elements as elements_cmd


class TestElementMoveCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"element_id": 5, "position": None, "parent_id": None}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_position_only(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(position=3), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/elements/5/move", {"position": 3})

    def test_parent_id_only(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(parent_id=99), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/elements/5/move", {"parent_id": 99})

    def test_both_fields(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = elements_cmd.run(self._args(position=1, parent_id=99), client)
        self.assertEqual(rc, 0)
        sent = client.put.call_args[0][1]
        self.assertEqual(sent["position"], 1)
        self.assertEqual(sent["parent_id"], 99)

    def test_neither_field_errors(self):
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
            rc = elements_cmd.run(self._args(position=1), client)
        self.assertEqual(rc, 1)
        self.assertIn("422", err.getvalue())
