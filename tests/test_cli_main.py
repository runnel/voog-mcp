"""Tests for voog.cli.main — argparse dispatch and exit codes."""
import sys
import unittest
from io import StringIO
from unittest.mock import patch

from voog.cli.main import build_parser, main


class TestBuildParser(unittest.TestCase):
    def test_global_site_flag_exists(self):
        parser = build_parser()
        # parse a known subcommand; --site is a global flag
        args = parser.parse_args(["--site", "stella", "list"])
        self.assertEqual(args.site, "stella")

    def test_site_flag_optional(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        self.assertIsNone(args.site)

    def test_no_command_prints_help(self):
        parser = build_parser()
        with patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args([])
            # argparse exits 2 when required subcommand is missing
            self.assertEqual(ctx.exception.code, 2)


class TestMainExitCodes(unittest.TestCase):
    def test_unknown_command_exits_2(self):
        with patch("sys.argv", ["voog", "no_such_command"]):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
