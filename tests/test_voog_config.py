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
    find_cwd_config,
    find_repo_site_pointer,
    load_env_file,
    load_global_config,
    load_merged_config,
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
        site = resolve_site(cfg, flag_site="stella")
        self.assertEqual(site.name, "stella")

    def test_default_site_used_when_no_flag(self):
        cfg = self._make_global(default_site="stella")
        site = resolve_site(cfg, flag_site=None)
        self.assertEqual(site.name, "stella")

    def test_no_resolution_raises(self):
        cfg = self._make_global(default_site=None)
        with self.assertRaises(ConfigError):
            resolve_site(cfg, flag_site=None)

    def test_unknown_flag_site_raises_unknown_site_error(self):
        cfg = self._make_global()
        with self.assertRaises(UnknownSiteError) as ctx:
            resolve_site(cfg, flag_site="nonexistent")
        # Error must list available site names for actionable hint
        self.assertIn("stella", str(ctx.exception))
        self.assertIn("runnel", str(ctx.exception))


class TestRepoSitePointerLegacyFormat(unittest.TestCase):
    """Both forms of voog-site.json must parse + emit a DeprecationWarning."""

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

    def test_voog_site_json_modern_form_emits_deprecation_warning(self):
        """{"site": "X"} works but warns the user to migrate to voog.json."""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "voog-site.json").write_text(json.dumps({"site": "stella"}))
            with patch("warnings.warn") as mock_warn:
                pointer = find_repo_site_pointer(Path(tmp))
                self.assertEqual(pointer.site_name, "stella")
                mock_warn.assert_called_once()
                args, kwargs = mock_warn.call_args
                self.assertIn("voog.json", args[0])
                self.assertIn("default_site", args[0])
                self.assertIs(args[1], DeprecationWarning)


class TestFindCwdConfig(unittest.TestCase):
    """Walk up cwd looking for voog.json; skip the home location."""

    def test_finds_voog_json_in_cwd(self):
        with TemporaryDirectory() as tmp:
            cwd_cfg = Path(tmp) / "voog.json"
            cwd_cfg.write_text(json.dumps({"sites": {}}))
            found = find_cwd_config(Path(tmp), home_path=Path("/nonexistent/home/voog.json"))
            # find_cwd_config resolves the cwd, so compare resolved paths
            # (on macOS /var/folders/... → /private/var/folders/...).
            self.assertEqual(found, cwd_cfg.resolve())

    def test_walks_up_parents(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "voog.json").write_text(json.dumps({"sites": {}}))
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            found = find_cwd_config(deep, home_path=Path("/nonexistent/home/voog.json"))
            self.assertEqual(found, (root / "voog.json").resolve())

    def test_returns_none_when_no_voog_json_above_cwd(self):
        with TemporaryDirectory() as tmp:
            found = find_cwd_config(Path(tmp), home_path=Path("/nonexistent/home/voog.json"))
            self.assertIsNone(found)

    def test_excludes_home_path(self):
        """When walking up hits the home config dir, that match must be skipped."""
        with TemporaryDirectory() as tmp:
            home_cfg = Path(tmp) / "voog.json"
            home_cfg.write_text(json.dumps({"sites": {}}))
            # cwd == home dir; with home_path pointing at the same file, skip it
            found = find_cwd_config(Path(tmp), home_path=home_cfg)
            self.assertIsNone(found)


class TestLoadMergedConfig(unittest.TestCase):
    """Home + cwd voog.json deep-merge, cwd wins."""

    def _write(self, path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def test_load_merged_config_only_home(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {"stella": {"host": "s.com", "api_key_env": "S_KEY"}},
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(cfg.default_site, "stella")
            self.assertEqual(set(cfg.sites), {"stella"})

    def test_load_merged_config_only_cwd(self):
        """Edge case: home config absent, cwd config provides everything."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"  # not created
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(
                cwd / "voog.json",
                {
                    "sites": {"runnel": {"host": "r.com", "api_key": "vk_inline"}},
                    "default_site": "runnel",
                },
            )
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(cfg.default_site, "runnel")
            self.assertEqual(cfg.sites["runnel"].api_key, "vk_inline")

    def test_load_merged_config_cwd_overrides_default_site(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {
                    "sites": {
                        "stella": {"host": "s.com", "api_key_env": "S_KEY"},
                        "runnel": {"host": "r.com", "api_key_env": "R_KEY"},
                    },
                    "default_site": "stella",
                },
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(cwd / "voog.json", {"default_site": "runnel"})
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(cfg.default_site, "runnel")
            # Both sites still reachable (sites map merged from home)
            self.assertEqual(set(cfg.sites), {"stella", "runnel"})

    def test_load_merged_config_cwd_adds_new_site(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {"sites": {"stella": {"host": "s.com", "api_key_env": "S_KEY"}}},
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(
                cwd / "voog.json",
                {
                    "sites": {"client_a": {"host": "ca.com", "api_key": "vk_ca"}},
                    "default_site": "client_a",
                },
            )
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(set(cfg.sites), {"stella", "client_a"})
            self.assertEqual(cfg.default_site, "client_a")
            self.assertEqual(cfg.sites["client_a"].api_key, "vk_ca")

    def test_load_merged_config_cwd_overrides_site_token(self):
        """If a site name appears in both, the cwd entry wins entirely."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"
            self._write(
                home,
                {"sites": {"stella": {"host": "old.com", "api_key_env": "OLD_KEY"}}},
            )
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            self._write(
                cwd / "voog.json",
                {"sites": {"stella": {"host": "new.com", "api_key": "vk_new"}}},
            )
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(cfg.sites["stella"].host, "new.com")
            self.assertEqual(cfg.sites["stella"].api_key, "vk_new")
            self.assertIsNone(cfg.sites["stella"].api_key_env)

    def test_load_merged_config_neither_present(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home" / "voog.json"  # not created
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            cfg = load_merged_config(cwd=cwd, home_path=home)
            self.assertEqual(cfg.sites, {})
            self.assertIsNone(cfg.default_site)


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
