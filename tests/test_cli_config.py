"""Tests for voog config subcommands."""

import json
import os
import stat
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog.cli.commands import config as config_cmd
from voog.config import GlobalConfig, SiteConfig


class TestListSites(unittest.TestCase):
    def _patch_loaders(self, merged: GlobalConfig, home: GlobalConfig | None = None):
        """Patch both loaders the new list_sites uses (merged + home for diff)."""
        home_cfg = home if home is not None else merged
        return patch.multiple(
            "voog.cli.commands.config",
            load_merged_config=lambda **_: merged,
            load_global_config=lambda *a, **kw: home_cfg,
            find_cwd_config=lambda *a, **kw: None,
        )

    def test_prints_each_site(self):
        cfg = GlobalConfig(
            sites={
                "alpha": SiteConfig(name="alpha", host="a.com", api_key_env="A"),
                "beta": SiteConfig(name="beta", host="b.com", api_key_env="B"),
            },
            default_site="alpha",
        )
        with self._patch_loaders(cfg), patch("sys.stdout", new_callable=StringIO) as stdout:
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
        with self._patch_loaders(cfg), patch("sys.stdout", new_callable=StringIO) as stdout:
            args = type("Args", (), {"config": None})()
            rc = config_cmd.list_sites(args)
        self.assertEqual(rc, 0)
        self.assertIn("no sites configured", stdout.getvalue().lower())


class TestInitSecurityHardening(unittest.TestCase):
    """voog config init now writes a plaintext-secret file —
    permissions and warnings must be set."""

    def _run_init(self, cfg_path: Path, inputs: list[str]):
        args = type("Args", (), {"config": cfg_path})()
        with patch("builtins.input", side_effect=inputs):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                with patch("sys.stderr", new_callable=StringIO) as stderr:
                    rc = config_cmd.init(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    @unittest.skipIf(os.name != "posix", "chmod semantics differ on Windows")
    def test_init_chmods_to_0600(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            rc, _, _ = self._run_init(
                cfg_path,
                inputs=["mysite", "mysite.com", "vk_test_token", ""],
            )
            self.assertEqual(rc, 0)
            self.assertTrue(cfg_path.exists())
            mode = cfg_path.stat().st_mode & 0o777
            self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR, f"expected 0600, got {oct(mode)}")

    def test_init_writes_security_note_to_stderr(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            rc, _, stderr = self._run_init(
                cfg_path,
                inputs=["mysite", "mysite.com", "vk_test_token", ""],
            )
            self.assertEqual(rc, 0)
            # Must mention the plaintext-token risk and the env-var alternative
            self.assertIn("plaintext", stderr.lower())
            self.assertIn("api_key_env", stderr)

    def test_init_writes_valid_json(self):
        """Sanity check: the file written by init parses with the loader."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            self._run_init(
                cfg_path,
                inputs=["mysite", "mysite.com", "vk_test_token", ""],
            )
            data = json.loads(cfg_path.read_text())
            self.assertEqual(data["sites"]["mysite"]["host"], "mysite.com")
            self.assertEqual(data["sites"]["mysite"]["api_key"], "vk_test_token")
            self.assertEqual(data["default_site"], "mysite")

    def test_init_multi_site_default_prompt(self):
        """When more than one site is configured, init prompts for default."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            rc, _, _ = self._run_init(
                cfg_path,
                inputs=[
                    "alpha",
                    "alpha.com",
                    "vk_a",
                    "beta",
                    "beta.com",
                    "vk_b",
                    "",  # stop adding sites
                    "alpha",  # answer to "Default site (blank for none): "
                ],
            )
            self.assertEqual(rc, 0)
            data = json.loads(cfg_path.read_text())
            self.assertEqual(set(data["sites"]), {"alpha", "beta"})
            self.assertEqual(data["default_site"], "alpha")

    def test_init_multi_site_invalid_default_errors(self):
        """If user types a default that isn't in sites, init errors out."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            rc, _, stderr = self._run_init(
                cfg_path,
                inputs=[
                    "alpha",
                    "alpha.com",
                    "vk_a",
                    "beta",
                    "beta.com",
                    "vk_b",
                    "",
                    "ghost",  # not a site
                ],
            )
            self.assertEqual(rc, 1)
            self.assertIn("not in sites", stderr)
            # Aborted before writing the file
            self.assertFalse(cfg_path.exists())


if __name__ == "__main__":
    unittest.main()
