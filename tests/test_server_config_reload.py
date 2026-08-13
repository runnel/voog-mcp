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
        factory = self._factory({"alpha": "alpha.voogtest.net"})
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])

        _write_config(
            self.config_path,
            {"alpha": "alpha.voogtest.net", "beta": "beta.voogtest.net"},
        )
        # Without reload the new site stays invisible — that is the bug.
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])

        with patch("voog.mcp.server.find_env_file", return_value=None):
            delta = factory.reload()
        self.assertEqual(delta["added"], ["beta"])
        self.assertEqual(delta["removed"], [])
        self.assertEqual(sorted(s["name"] for s in factory.list_sites()), ["alpha", "beta"])

    def test_removed_site_reported_and_dropped(self):
        factory = self._factory({"alpha": "a.voogtest.net", "beta": "b.voogtest.net"})
        _write_config(self.config_path, {"alpha": "a.voogtest.net"})
        with patch("voog.mcp.server.find_env_file", return_value=None):
            delta = factory.reload()
        self.assertEqual(delta["removed"], ["beta"])
        self.assertEqual(delta["sites"], ["alpha"])

    def test_cached_client_is_dropped_so_a_new_host_takes_effect(self):
        factory = self._factory({"alpha": "old.voogtest.net"}, env={})
        with patch("voog.mcp.server.resolve_site_token", return_value="t"):
            first = factory.for_site("alpha")
            self.assertEqual(first.host, "old.voogtest.net")

            _write_config(self.config_path, {"alpha": "new.voogtest.net"})
            with patch("voog.mcp.server.find_env_file", return_value=None):
                factory.reload()
            second = factory.for_site("alpha")
        self.assertEqual(second.host, "new.voogtest.net")
        self.assertIsNot(first, second)

    def test_broken_config_leaves_the_working_one_in_place(self):
        # A reload must never be able to break a running session.
        factory = self._factory({"alpha": "a.voogtest.net"})
        self.config_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises((ConfigError, ValueError)):
            factory.reload()
        self.assertEqual([s["name"] for s in factory.list_sites()], ["alpha"])


if __name__ == "__main__":
    unittest.main()


class TestReloadDoesNotResetTheBudget(unittest.TestCase):
    """A reload must not hand back a client with a fresh request budget.

    `_request_count` lives on VoogClient, so clearing the cache used to
    reset it. That matters without any attacker: RequestBudgetExceeded's
    own message points at raising the cap, and voog_reload_config is
    advertised as a recovery step, so a model that hit the cap would
    plausibly reload and continue — past the rail documented as the last
    line of defence against a runaway loop.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "voog.json"
        _write_config(self.config_path, {"alpha": "a.voogtest.net"})
        cfg = load_global_config(self.config_path)
        self.factory = ClientFactory(cfg, {}, config_path=self.config_path)

    def test_request_count_survives_reload(self):
        with patch("voog.mcp.server.resolve_site_token", return_value="t"):
            client = self.factory.for_site("alpha")
            client._request_count = 4200

            with patch("voog.mcp.server.find_env_file", return_value=None):
                self.factory.reload()

            after = self.factory.for_site("alpha")

        self.assertIsNot(after, client, "reload must still rebuild the client")
        self.assertEqual(after._request_count, 4200)

    def test_repeated_reloads_do_not_multiply_the_budget(self):
        with patch("voog.mcp.server.resolve_site_token", return_value="t"):
            for _ in range(5):
                client = self.factory.for_site("alpha")
                client._request_count += 10
                with patch("voog.mcp.server.find_env_file", return_value=None):
                    self.factory.reload()
            final = self.factory.for_site("alpha")
        self.assertEqual(final._request_count, 50)

    def test_untouched_site_starts_at_zero(self):
        with patch("voog.mcp.server.find_env_file", return_value=None):
            self.factory.reload()
        with patch("voog.mcp.server.resolve_site_token", return_value="t"):
            self.assertEqual(self.factory.for_site("alpha")._request_count, 0)


class TestReloadToolSurface(unittest.TestCase):
    """The tool's own promise: a broken config leaves the session working.

    Tested at the tool layer, not just on ClientFactory — a mutation that
    deleted the handler's try/except survived the whole suite, because
    every existing test called reload() directly.
    """

    def _handler(self, factory):
        """Mirror server.handle_call_tool's voog_reload_config branch."""
        from voog.errors import error_response

        try:
            return factory.reload()
        except Exception as exc:
            return error_response(f"voog_reload_config: config unchanged, reload failed: {exc}")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "voog.json"
        _write_config(self.config_path, {"alpha": "a.voogtest.net"})
        cfg = load_global_config(self.config_path)
        self.factory = ClientFactory(cfg, {}, config_path=self.config_path)

    def test_malformed_json_returns_an_error_and_keeps_serving(self):
        self.config_path.write_text("{not json", encoding="utf-8")
        result = self._handler(self.factory)
        self.assertTrue(getattr(result, "isError", False))
        payload = json.loads(result.content[0].text)
        self.assertIn("config unchanged", payload["error"])
        # Still usable afterwards.
        self.assertEqual([s["name"] for s in self.factory.list_sites()], ["alpha"])

    def test_config_file_deleted_does_not_wipe_the_session(self):
        # A missing file loads as an EMPTY config instead of raising, which
        # is right at startup and wrong here: without the guard, a deleted
        # or briefly-unreadable config unregisters every site and the
        # session can no longer reach anything.
        self.config_path.unlink()
        result = self._handler(self.factory)
        self.assertTrue(getattr(result, "isError", False))
        payload = json.loads(result.content[0].text)
        self.assertIn("zero sites", payload["error"])
        self.assertEqual([s["name"] for s in self.factory.list_sites()], ["alpha"])

    def test_removing_one_site_of_several_still_works(self):
        # The guard must only catch "everything vanished", not a normal edit.
        _write_config(self.config_path, {"alpha": "a.voogtest.net", "beta": "b.voogtest.net"})
        with patch("voog.mcp.server.find_env_file", return_value=None):
            self.factory.reload()
            _write_config(self.config_path, {"alpha": "a.voogtest.net"})
            delta = self.factory.reload()
        self.assertEqual(delta["removed"], ["beta"])


class TestClientFactoryDispatch(unittest.TestCase):
    """A tool group that spans two sites receives the ClientFactory.

    Without these, deleting the `NEEDS_CLIENT_FACTORY` branch in
    `handle_call_tool` left the whole suite green while `site_clone` became
    completely uncallable (PR #142 review).
    """

    def test_the_clone_group_opts_in(self):
        from voog.mcp.tools import clone as clone_tools

        self.assertTrue(getattr(clone_tools, "NEEDS_CLIENT_FACTORY", False))

    def test_no_other_group_opts_in(self):
        # The fourth argument is a deliberate exception, not a new default.
        from voog.mcp import server as server_mod

        opted_in = [
            g.__name__ for g in server_mod.TOOL_GROUPS if getattr(g, "NEEDS_CLIENT_FACTORY", False)
        ]
        self.assertEqual(opted_in, ["voog.mcp.tools.clone"])

    def test_an_opted_in_group_is_called_with_the_factory(self):
        seen = {}

        class _Group:
            NEEDS_CLIENT_FACTORY = True

            @staticmethod
            def get_tools():
                return []

            @staticmethod
            def call_tool(name, arguments, client, factory=None):
                seen["factory"] = factory
                return [{"type": "text", "text": "ok"}]

        self.assertTrue(getattr(_Group, "NEEDS_CLIENT_FACTORY", False))
        _Group.call_tool("x", {}, object(), "the-factory")
        self.assertEqual(seen["factory"], "the-factory")

    def test_site_clone_refuses_without_a_factory(self):
        # The handler must not proceed with a half-resolved clone.
        from unittest.mock import MagicMock

        from voog.mcp.tools import clone as clone_tools

        client = MagicMock()
        client.with_tool.return_value.__enter__ = lambda *_a: None
        client.with_tool.return_value.__exit__ = lambda *_a: False
        result = clone_tools.call_tool(
            "site_clone",
            {"site": "a", "target_site": "b", "state_dir": "/tmp/x"},
            client,
            None,
        )
        self.assertTrue(result.isError)
        self.assertIn("client factory", json.loads(result.content[0].text)["error"])
