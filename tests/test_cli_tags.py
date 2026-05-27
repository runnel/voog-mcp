"""Tests for voog tags CLI subcommands."""

import json
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from voog.cli.commands import tags as tags_cmd


class TestTagsListCLI(unittest.TestCase):
    def test_lists_tags(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "name": "leather", "taggings_count": 12},
        ]
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = tags_cmd.cmd_tags_list(type("Args", (), {})(), client)
        self.assertEqual(rc, 0)
        self.assertIn("leather", out.getvalue())


class TestTagGetCLI(unittest.TestCase):
    def test_prints_json(self):
        client = MagicMock()
        client.get.return_value = {"id": 5, "name": "tutorial"}
        args = type("Args", (), {"tag_id": 5})()
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = tags_cmd.cmd_tag_get(args, client)
        self.assertEqual(rc, 0)
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["name"], "tutorial")


class TestTagDeleteCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"tag_id": 5, "force": False}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_force_skips_confirm(self):
        client = MagicMock()
        with patch("sys.stdout", new_callable=StringIO):
            rc = tags_cmd.cmd_tag_delete(self._args(force=True), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once_with("/tags/5")

    def test_confirm_yes_deletes(self):
        client = MagicMock()
        client.get.return_value = {"id": 5, "name": "leather", "taggings_count": 12}
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout", new_callable=StringIO):
                rc = tags_cmd.cmd_tag_delete(self._args(), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once_with("/tags/5")

    def test_confirm_no_aborts(self):
        client = MagicMock()
        client.get.return_value = {"id": 5}
        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", new_callable=StringIO):
                rc = tags_cmd.cmd_tag_delete(self._args(), client)
        self.assertEqual(rc, 0)
        client.delete.assert_not_called()
