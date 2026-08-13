"""Tests for ClientFactory.reload — issue #140 item 1.

The server used to read voog.json once at startup, so a site registered
mid-session was invisible until the MCP host restarted. That is what pushed a
whole site-duplication job onto the CLI.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voog.config import ConfigError, load_global_config
from voog.mcp.server import ClientFactory


def _write_config(path: Path, sites: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "sites": {
                    name: {"host": host, "api_key_env": f"{name.upper()}_TOKEN"}
                    for name, host in sites.items()
                }
            }
        ),
        encoding="utf-8",
    )


class TestClientFactoryReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "voog.json"

    def _factory(self, sites, env=None):
        _write_config(self.config_path, sites)
        cfg = load_global_config(self.config_path)
        return ClientFactory(cfg, env or {}, config_path=self.config_path)

    def test_site_added_after_startup_becomes_visible(self):
        factory = self._factory({"alpha": "alpha.example.com"})
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])

        _write_config(
            self.config_path,
            {"alpha": "alpha.example.com", "beta": "beta.example.com"},
        )
        # Without reload the new site stays invisible — that is the bug.
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])

        with patch("voog.mcp.server.find_env_file", return_value=None):
            delta = factory.reload()
        self.assertEqual(delta["added"], ["beta"])
        self.assertEqual(delta["removed"], [])
        self.assertEqual(sorted(s["name"] for s in factory.list_sites()), ["alpha", "beta"])

    def test_removed_site_reported_and_dropped(self):
        factory = self._factory({"alpha": "a.example.com", "beta": "b.example.com"})
        _write_config(self.config_path, {"alpha": "a.example.com"})
        with patch("voog.mcp.server.find_env_file", return_value=None):
            delta = factory.reload()
        self.assertEqual(delta["removed"], ["beta"])
        self.assertEqual(delta["sites"], ["alpha"])

    def test_cached_client_is_dropped_so_a_new_host_takes_effect(self):
        factory = self._factory({"alpha": "old.example.com"}, env={})
        with patch("voog.mcp.server.resolve_site_token", return_value="t"):
            first = factory.for_site("alpha")
            self.assertEqual(first.host, "old.example.com")

            _write_config(self.config_path, {"alpha": "new.example.com"})
            with patch("voog.mcp.server.find_env_file", return_value=None):
                factory.reload()
            second = factory.for_site("alpha")
        self.assertEqual(second.host, "new.example.com")
        self.assertIsNot(first, second)

    def test_broken_config_leaves_the_working_one_in_place(self):
        # A reload must never be able to break a running session.
        factory = self._factory({"alpha": "a.example.com"})
        self.config_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises((ConfigError, ValueError)):
            factory.reload()
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])


if __name__ == "__main__":
    unittest.main()
