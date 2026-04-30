"""Tests for voog.config — multi-site resolution."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog.config import (
    ConfigError,
    GlobalConfig,
    SiteConfig,
    UnknownSiteError,
    find_repo_site_pointer,
    load_env_file,
    load_global_config,
    resolve_site,
    resolve_site_token,
)


class TestLoadGlobalConfig(unittest.TestCase):
    def test_loads_valid_config(self):
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sites": {
                            "site_a": {"host": "a.example.com", "api_key_env": "A_KEY"},
                            "site_b": {"host": "b.example.com", "api_key_env": "B_KEY"},
                        },
                        "default_site": "site_a",
                    }
                )
            )
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
            cfg_path.write_text(
                json.dumps(
                    {
                        "sites": {"x": {"host": "x.com"}},  # neither api_key nor api_key_env
                    }
                )
            )
            with self.assertRaises(ConfigError):
                load_global_config(cfg_path)

    def test_site_config_accepts_api_key_inline(self):
        """New format: token sits directly in voog.json under api_key."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sites": {
                            "x": {"host": "x.com", "api_key": "vk_inline_token"},
                        }
                    }
                )
            )
            cfg = load_global_config(cfg_path)
            self.assertEqual(cfg.sites["x"].api_key, "vk_inline_token")
            self.assertIsNone(cfg.sites["x"].api_key_env)

    def test_site_config_accepts_api_key_env(self):
        """Old format: env-var-name reference still works."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sites": {
                            "x": {"host": "x.com", "api_key_env": "X_KEY"},
                        }
                    }
                )
            )
            cfg = load_global_config(cfg_path)
            self.assertEqual(cfg.sites["x"].api_key_env, "X_KEY")
            self.assertIsNone(cfg.sites["x"].api_key)

    def test_site_config_accepts_both(self):
        """Both fields populated is valid — env-var path wins at resolve time."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sites": {
                            "x": {
                                "host": "x.com",
                                "api_key": "vk_inline",
                                "api_key_env": "X_KEY",
                            }
                        }
                    }
                )
            )
            cfg = load_global_config(cfg_path)
            self.assertEqual(cfg.sites["x"].api_key, "vk_inline")
            self.assertEqual(cfg.sites["x"].api_key_env, "X_KEY")

    def test_site_config_rejects_neither_set(self):
        """Both api_key and api_key_env missing must raise a clear error."""
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "voog.json"
            cfg_path.write_text(json.dumps({"sites": {"x": {"host": "x.com"}}}))
            with self.assertRaises(ConfigError) as ctx:
                load_global_config(cfg_path)
            msg = str(ctx.exception)
            self.assertIn("api_key", msg)
            self.assertIn("api_key_env", msg)


class TestResolveSiteToken(unittest.TestCase):
    def test_inline_api_key_used_when_only_one_set(self):
        site = SiteConfig(name="x", host="x.com", api_key="vk_inline")
        self.assertEqual(resolve_site_token(site, env={}), "vk_inline")

    def test_env_var_used_when_only_one_set(self):
        site = SiteConfig(name="x", host="x.com", api_key_env="X_KEY")
        self.assertEqual(resolve_site_token(site, env={"X_KEY": "from_env"}), "from_env")

    def test_env_var_wins_when_both_set(self):
        """Documented behavior: env-var beats inline so a checked-in
        config can carry a default and the environment can override."""
        site = SiteConfig(name="x", host="x.com", api_key="vk_inline", api_key_env="X_KEY")
        token = resolve_site_token(site, env={"X_KEY": "from_env"})
        self.assertEqual(token, "from_env")

    def test_falls_back_to_inline_when_env_var_unset(self):
        """If api_key_env is named but not actually set, use inline as fallback."""
        site = SiteConfig(name="x", host="x.com", api_key="vk_inline", api_key_env="X_KEY")
        with patch.dict("os.environ", {}, clear=True):
            token = resolve_site_token(site, env={})
            self.assertEqual(token, "vk_inline")

    def test_raises_when_env_var_unset_and_no_inline(self):
        site = SiteConfig(name="x", host="x.com", api_key_env="X_KEY")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                resolve_site_token(site, env={})
            self.assertIn("X_KEY", str(ctx.exception))

    def test_reads_from_os_environ_if_not_in_env_dict(self):
        site = SiteConfig(name="x", host="x.com", api_key_env="X_KEY")
        with patch.dict("os.environ", {"X_KEY": "from_os"}):
            token = resolve_site_token(site, env={})
            self.assertEqual(token, "from_os")


class TestResolveSite(unittest.TestCase):
    def _make_global(self, default_site=None):
        return GlobalConfig(
            sites={
                "stella": SiteConfig(name="stella", host="mysite.com", api_key_env="VOOG_API_KEY"),
                "runnel": SiteConfig(name="runnel", host="example.com", api_key_env="RUNNEL_KEY"),
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
            (Path(tmp) / "voog-site.json").write_text(
                json.dumps(
                    {
                        "host": "legacy.example.com",
                        "api_key_env": "LEGACY_KEY",
                    }
                )
            )
            with patch("warnings.warn") as mock_warn:
                pointer = find_repo_site_pointer(Path(tmp))
                self.assertEqual(pointer.legacy_host, "legacy.example.com")
                self.assertEqual(pointer.legacy_api_key_env, "LEGACY_KEY")
                self.assertIsNone(pointer.site_name)
                mock_warn.assert_called_once()


class TestClientFactoryTokenResolution(unittest.TestCase):
    """Black-box: server's ClientFactory must accept inline-keyed sites."""

    def test_client_factory_resolves_inline_api_key(self):
        from voog.mcp.server import ClientFactory

        cfg = GlobalConfig(
            sites={
                "x": SiteConfig(name="x", host="x.example.com", api_key="vk_inline"),
            }
        )
        factory = ClientFactory(cfg, env={})
        client = factory.for_site("x")
        # VoogClient stores host/token; we verify the inline token reached it.
        self.assertEqual(client.host, "x.example.com")
        self.assertEqual(client.api_token, "vk_inline")

    def test_client_factory_resolves_env_var(self):
        from voog.mcp.server import ClientFactory

        cfg = GlobalConfig(
            sites={
                "x": SiteConfig(name="x", host="x.example.com", api_key_env="X_KEY"),
            }
        )
        factory = ClientFactory(cfg, env={"X_KEY": "from_env"})
        client = factory.for_site("x")
        self.assertEqual(client.api_token, "from_env")

    def test_client_factory_env_var_wins_over_inline(self):
        from voog.mcp.server import ClientFactory

        cfg = GlobalConfig(
            sites={
                "x": SiteConfig(
                    name="x",
                    host="x.example.com",
                    api_key="vk_inline",
                    api_key_env="X_KEY",
                ),
            }
        )
        factory = ClientFactory(cfg, env={"X_KEY": "from_env"})
        client = factory.for_site("x")
        self.assertEqual(client.api_token, "from_env")


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
