"""Tests for voog.config — multi-site resolution."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog.config import (
    SiteConfig,
    GlobalConfig,
    ConfigError,
    UnknownSiteError,
    load_global_config,
    resolve_site,
    find_repo_site_pointer,
    load_env_file,
)


class TestLoadGlobalConfig(unittest.TestCase):
    def test_loads_valid_config(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(json.dumps({
                "sites": {
                    "site_a": {"host": "a.example.com", "api_key_env": "A_KEY"},
                    "site_b": {"host": "b.example.com", "api_key_env": "B_KEY"},
                },
                "default_site": "site_a",
            }))
            cfg = load_global_config(cfg_path)
            self.assertEqual(cfg.default_site, "site_a")
            self.assertEqual(cfg.sites["site_a"].host, "a.example.com")
            self.assertEqual(cfg.sites["site_b"].api_key_env, "B_KEY")

    def test_missing_file_returns_empty(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg = load_global_config(cfg_path)
            self.assertEqual(cfg.sites, {})
            self.assertIsNone(cfg.default_site)

    def test_malformed_json_raises_config_error(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text("{ not valid json")
            with self.assertRaises(ConfigError):
                load_global_config(cfg_path)

    def test_missing_required_site_field_raises(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(json.dumps({
                "sites": {"x": {"host": "x.com"}},  # api_key_env missing
            }))
            with self.assertRaises(ConfigError):
                load_global_config(cfg_path)


class TestResolveSite(unittest.TestCase):
    def _make_global(self, default_site=None):
        return GlobalConfig(
            sites={
                "stella": SiteConfig(name="stella", host="stellasoomlais.com", api_key_env="VOOG_API_KEY"),
                "runnel": SiteConfig(name="runnel", host="runnel.ee", api_key_env="RUNNEL_KEY"),
            },
            default_site=default_site,
            env_file=None,
        )

    def test_explicit_flag_wins(self):
        cfg = self._make_global(default_site="runnel")
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "voog-site.json").write_text(json.dumps({"site": "runnel"}))
            site = resolve_site(cfg, flag_site="stella", cwd=Path(tmp))
            self.assertEqual(site.name, "stella")

    def test_repo_pointer_used_when_no_flag(self):
        cfg = self._make_global()
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "voog-site.json").write_text(json.dumps({"site": "runnel"}))
            site = resolve_site(cfg, flag_site=None, cwd=Path(tmp))
            self.assertEqual(site.name, "runnel")

    def test_repo_pointer_walks_up_parents(self):
        cfg = self._make_global()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "voog-site.json").write_text(json.dumps({"site": "stella"}))
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            site = resolve_site(cfg, flag_site=None, cwd=deep)
            self.assertEqual(site.name, "stella")

    def test_default_site_used_when_no_flag_no_pointer(self):
        cfg = self._make_global(default_site="stella")
        with TemporaryDirectory() as tmp:
            site = resolve_site(cfg, flag_site=None, cwd=Path(tmp))
            self.assertEqual(site.name, "stella")

    def test_no_resolution_raises(self):
        cfg = self._make_global(default_site=None)
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                resolve_site(cfg, flag_site=None, cwd=Path(tmp))

    def test_unknown_flag_site_raises_unknown_site_error(self):
        cfg = self._make_global()
        with TemporaryDirectory() as tmp:
            with self.assertRaises(UnknownSiteError) as ctx:
                resolve_site(cfg, flag_site="nonexistent", cwd=Path(tmp))
            # Error must list available site names for actionable hint
            self.assertIn("stella", str(ctx.exception))
            self.assertIn("runnel", str(ctx.exception))


class TestRepoSitePointerLegacyFormat(unittest.TestCase):
    """Old format {host, api_key_env} must be parsed with deprecation warning."""

    def test_legacy_format_returns_synthetic_site(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "voog-site.json").write_text(json.dumps({
                "host": "legacy.example.com",
                "api_key_env": "LEGACY_KEY",
            }))
            with patch("warnings.warn") as mock_warn:
                pointer = find_repo_site_pointer(Path(tmp))
                self.assertEqual(pointer.legacy_host, "legacy.example.com")
                self.assertEqual(pointer.legacy_api_key_env, "LEGACY_KEY")
                self.assertIsNone(pointer.site_name)
                mock_warn.assert_called_once()


class TestLoadEnvFile(unittest.TestCase):
    def test_loads_simple_env(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("VOOG_API_KEY=abc123\nOTHER=value\n")
            env = load_env_file(env_path)
            self.assertEqual(env["VOOG_API_KEY"], "abc123")
            self.assertEqual(env["OTHER"], "value")

    def test_missing_file_returns_empty_dict(self):
        env = load_env_file(Path("/nonexistent/.env"))
        self.assertEqual(env, {})

    def test_skips_comments_and_blanks(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# comment\n\nKEY=value\n# another\n")
            env = load_env_file(env_path)
            self.assertEqual(env, {"KEY": "value"})


if __name__ == "__main__":
    unittest.main()
