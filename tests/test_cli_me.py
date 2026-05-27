"""Tests for voog list-my-sites CLI subcommand."""

import json
import unittest
from io import StringIO
from unittest.mock import patch

from voog.cli.commands import me as me_cmd


class TestListMySitesCLI(unittest.TestCase):
    def _args(self, **overrides):
        defaults = {"token_env": None, "token": None, "host": "www.voog.com"}
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_token_env(self):
        with patch.dict("os.environ", {"VOOG_TEST_TOKEN": "vk_real"}, clear=False):
            with patch("voog.cli.commands.me.VoogClient") as MockClient:
                MockClient.return_value.get.return_value = [
                    {
                        "name": "alpha",
                        "primary_domain": "alpha.com",
                        "feature_flags": ["has_ecommerce"],
                    }
                ]
                with patch("sys.stdout", new_callable=StringIO) as out:
                    rc = me_cmd.run(self._args(token_env="VOOG_TEST_TOKEN"))
        self.assertEqual(rc, 0)
        MockClient.assert_called_once_with(host="www.voog.com", api_token="vk_real")
        self.assertIn("alpha", out.getvalue())
        self.assertIn("has_ecommerce", out.getvalue())
        text = out.getvalue()
        # JSON dump appended for piping into jq — verify it parses by
        # extracting the trailing JSON array (find the last `\n[` which
        # marks the start of the pretty-printed dump, after the human-
        # readable summary block).
        json_start = text.rindex("\n[\n") + 1
        parsed = json.loads(text[json_start:])
        self.assertEqual(parsed[0]["name"], "alpha")

    def test_token_inline(self):
        with patch("voog.cli.commands.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            with patch("sys.stdout", new_callable=StringIO):
                rc = me_cmd.run(self._args(token="vk_inline"))
        self.assertEqual(rc, 0)
        MockClient.assert_called_once_with(host="www.voog.com", api_token="vk_inline")

    def test_token_env_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as err:
                rc = me_cmd.run(self._args(token_env="VOOG_NOT_SET"))
        self.assertEqual(rc, 1)
        self.assertIn("not set", err.getvalue())

    def test_token_env_set_but_empty(self):
        with patch.dict("os.environ", {"VOOG_EMPTY": ""}, clear=False):
            with patch("sys.stderr", new_callable=StringIO) as err:
                rc = me_cmd.run(self._args(token_env="VOOG_EMPTY"))
        self.assertEqual(rc, 1)
        self.assertIn("empty", err.getvalue().lower())

    def test_both_token_and_token_env_rejected(self):
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = me_cmd.run(self._args(token="x", token_env="Y"))
        self.assertEqual(rc, 1)
        self.assertIn("not both", err.getvalue())

    def test_no_token_at_all(self):
        with patch("sys.stderr", new_callable=StringIO) as err:
            rc = me_cmd.run(self._args())
        self.assertEqual(rc, 1)
        self.assertIn("supply", err.getvalue().lower())

    def test_r6_note_when_single_site(self):
        with patch("voog.cli.commands.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = [
                {"name": "alpha", "primary_domain": "alpha.com", "feature_flags": []}
            ]
            with patch("sys.stdout", new_callable=StringIO) as out:
                me_cmd.run(self._args(token="vk"))
        self.assertIn("site-scoped", out.getvalue())

    def test_custom_host_forwarded(self):
        # Real-world: tenant on their own primary domain.
        with patch("voog.cli.commands.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            with patch("sys.stdout", new_callable=StringIO):
                me_cmd.run(self._args(token="vk", host="stellasoomlais.com"))
        MockClient.assert_called_once_with(host="stellasoomlais.com", api_token="vk")

    def test_unexpected_shape(self):
        with patch("voog.cli.commands.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = {"not": "a list"}
            with patch("sys.stderr", new_callable=StringIO) as err:
                rc = me_cmd.run(self._args(token="vk"))
        self.assertEqual(rc, 1)
        self.assertIn("unexpected", err.getvalue().lower())
