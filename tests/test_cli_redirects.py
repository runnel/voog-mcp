"""Tests for voog.cli.commands.redirects — list + add.

Redirects are a thin layer over the API client. Tests verify the
payload shape (Voog wants ``redirect_rule`` envelope with
``destination`` not ``target``) and the default 301 status code.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from voog.cli.commands import redirects as redirects_cmd


def _make_client():
    return MagicMock()


def _args(**kwargs):
    args = MagicMock()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


class TestRedirectsList(unittest.TestCase):
    def test_lists_returns_zero_with_summary_table(self):
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "redirect_type": 301, "source": "/old", "destination": "/new"},
            {"id": 2, "redirect_type": 410, "source": "/gone", "destination": ""},
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = redirects_cmd.cmd_list(_args(), client)
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("/old", out)
        self.assertIn("/gone", out)
        self.assertIn("Total: 2", out)

    def test_empty_list_prints_friendly_message(self):
        client = _make_client()
        client.get_all.return_value = []
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = redirects_cmd.cmd_list(_args(), client)
        self.assertEqual(rc, 0)
        self.assertIn("No redirect rules found", stdout.getvalue())


class TestRedirectAdd(unittest.TestCase):
    def test_add_default_301_payload_shape(self):
        client = _make_client()
        client.post.return_value = {"id": 99}
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = redirects_cmd.cmd_add(
                _args(source="/old", target="/new", status_code=301),
                client,
            )
        self.assertEqual(rc, 0)
        client.post.assert_called_once_with(
            "/redirect_rules",
            {
                "redirect_rule": {
                    "source": "/old",
                    "destination": "/new",
                    "redirect_type": 301,
                    "active": True,
                    "regexp": False,
                }
            },
        )

    def test_add_410_gone_status_passes_through(self):
        client = _make_client()
        client.post.return_value = {"id": 99}
        with patch("sys.stdout", new_callable=io.StringIO):
            redirects_cmd.cmd_add(_args(source="/dead", target="", status_code=410), client)
        payload = client.post.call_args.args[1]
        self.assertEqual(payload["redirect_rule"]["redirect_type"], 410)


if __name__ == "__main__":
    unittest.main()
