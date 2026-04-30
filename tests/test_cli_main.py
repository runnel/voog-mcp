"""Tests for voog.cli.main — argparse dispatch and exit codes."""

import argparse
import json
import os
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog.cli.main import _build_client, build_parser, main
from voog.config import UnknownSiteError


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


class TestBuildClient(unittest.TestCase):
    """Direct coverage of the _build_client choreography (issue #71 flow).

    Each test sets up a tmpdir layout (home voog.json + optional cwd
    voog.json + optional voog-site.json), chdirs into it, and calls
    _build_client(args). The returned VoogClient's host/api_token
    proves which site was resolved.
    """

    def _args(self, config_path: Path, site: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(site=site, config=config_path)

    def _chdir(self, target: Path):
        original = Path.cwd()
        os.chdir(target)
        self.addCleanup(os.chdir, original)

    def _write(self, path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def test_resolves_default_site_from_home_only(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {"stella": {"host": "stella.com", "api_key": "vk_s"}},
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._chdir(cwd)
            client = _build_client(self._args(home))
            self.assertEqual(client.host, "stella.com")
            self.assertEqual(client.api_token, "vk_s")

    def test_cwd_voog_json_overrides_default_site(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {
                        "stella": {"host": "stella.com", "api_key": "vk_s"},
                        "runnel": {"host": "runnel.ee", "api_key": "vk_r"},
                    },
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog.json", {"default_site": "runnel"})
            self._chdir(cwd)
            client = _build_client(self._args(home))
            self.assertEqual(client.host, "runnel.ee")
            self.assertEqual(client.api_token, "vk_r")

    def test_cwd_voog_json_introduces_new_site(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {"sites": {"stella": {"host": "stella.com", "api_key": "vk_s"}}},
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(
                cwd / "voog.json",
                {
                    "sites": {"client_x": {"host": "cx.com", "api_key": "vk_x"}},
                    "default_site": "client_x",
                },
            )
            self._chdir(cwd)
            client = _build_client(self._args(home))
            self.assertEqual(client.host, "cx.com")
            self.assertEqual(client.api_token, "vk_x")

    def test_explicit_site_flag_wins_over_cwd_voog_json(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {
                        "stella": {"host": "stella.com", "api_key": "vk_s"},
                        "runnel": {"host": "runnel.ee", "api_key": "vk_r"},
                    },
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog.json", {"default_site": "runnel"})
            self._chdir(cwd)
            client = _build_client(self._args(home, site="stella"))
            # --site stella beats cwd voog.json's default_site=runnel
            self.assertEqual(client.host, "stella.com")

    def test_voog_site_json_modern_form_acts_as_implicit_site_flag(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {
                        "stella": {"host": "stella.com", "api_key": "vk_s"},
                        "runnel": {"host": "runnel.ee", "api_key": "vk_r"},
                    },
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog-site.json", {"site": "runnel"})
            self._chdir(cwd)
            with patch("warnings.warn"):  # suppress the deprecation warning in test
                client = _build_client(self._args(home))
            self.assertEqual(client.host, "runnel.ee")

    def test_explicit_site_flag_wins_over_voog_site_json(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {
                        "stella": {"host": "stella.com", "api_key": "vk_s"},
                        "runnel": {"host": "runnel.ee", "api_key": "vk_r"},
                    },
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog-site.json", {"site": "runnel"})
            self._chdir(cwd)
            with patch("warnings.warn"):
                client = _build_client(self._args(home, site="stella"))
            self.assertEqual(client.host, "stella.com")

    def test_voog_site_json_pointing_to_unknown_site_blames_pointer(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {"sites": {"stella": {"host": "stella.com", "api_key": "vk_s"}}},
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog-site.json", {"site": "ghost"})
            self._chdir(cwd)
            with patch("warnings.warn"):
                with self.assertRaises(UnknownSiteError) as ctx:
                    _build_client(self._args(home))
            # Error must blame voog-site.json, not --site
            self.assertIn("voog-site.json", str(ctx.exception))
            self.assertIn("ghost", str(ctx.exception))

    def test_legacy_voog_site_json_still_works_despite_malformed_cwd_voog_json(self):
        """BC: the legacy {host, api_key_env} path predates cwd voog.json.
        A malformed cwd voog.json above must NOT break it."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(home, {"sites": {}})  # empty home is fine
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            # Put a malformed voog.json a level UP — would crash load_merged_config
            (Path(tmp) / "voog.json").write_text("{ not valid json")
            self._write(
                cwd / "voog-site.json",
                {"host": "legacy.example.com", "api_key_env": "LEGACY_KEY"},
            )
            self._chdir(cwd)
            with patch.dict(os.environ, {"LEGACY_KEY": "vk_legacy"}):
                with patch("warnings.warn"):
                    client = _build_client(self._args(home))
            self.assertEqual(client.host, "legacy.example.com")
            self.assertEqual(client.api_token, "vk_legacy")


if __name__ == "__main__":
    unittest.main()
