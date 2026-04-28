"""Tests for voog config subcommands."""

import unittest
from io import StringIO
from unittest.mock import patch

from voog.cli.commands import config as config_cmd
from voog.config import GlobalConfig, SiteConfig


class TestListSites(unittest.TestCase):
    def test_prints_each_site(self):
        cfg = GlobalConfig(
            sites={
                "alpha": SiteConfig(name="alpha", host="a.com", api_key_env="A"),
                "beta": SiteConfig(name="beta", host="b.com", api_key_env="B"),
            },
            default_site="alpha",
        )
        with patch("voog.cli.commands.config.load_global_config", return_value=cfg):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                args = type("Args", (), {"config": None})()
                rc = config_cmd.list_sites(args)
        out = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("alpha", out)
        self.assertIn("a.com", out)
        self.assertIn("beta", out)
        self.assertIn("(default)", out)

    def test_no_sites_prints_message(self):
        cfg = GlobalConfig()
        with patch("voog.cli.commands.config.load_global_config", return_value=cfg):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                args = type("Args", (), {"config": None})()
                rc = config_cmd.list_sites(args)
        self.assertEqual(rc, 0)
        self.assertIn("no sites configured", stdout.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
