"""Tests for voog.mcp.tools.me — voog_list_my_sites discovery."""

import json
import unittest
from unittest.mock import patch

from voog.mcp.tools import me as mt


class TestGetTools(unittest.TestCase):
    def test_one_tool_registered(self):
        names = sorted(t.name for t in mt.get_tools())
        self.assertEqual(names, ["voog_list_my_sites"])

    def test_no_site_required(self):
        tool = next(t for t in mt.get_tools() if t.name == "voog_list_my_sites")
        # This tool intentionally has empty `required`.
        self.assertEqual(tool.inputSchema.get("required", []), [])

    def test_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["voog_list_my_sites"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestVoogListMySites(unittest.TestCase):
    def test_token_env_resolved_from_env(self):
        with patch.dict("os.environ", {"VOOG_TEST_TOKEN": "vk_secret"}, clear=False):
            with patch("voog.mcp.tools.me.VoogClient") as MockClient:
                instance = MockClient.return_value
                instance.get.return_value = [
                    {"name": "alpha", "primary_domain": "alpha.com", "feature_flags": []}
                ]
                result = mt.call_tool(
                    "voog_list_my_sites",
                    {"token_env": "VOOG_TEST_TOKEN"},
                    None,
                )
        MockClient.assert_called_once_with(host="www.voog.com", api_token="vk_secret")
        instance.get.assert_called_once_with("/me/sites")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["name"], "alpha")

    def test_token_inline_works(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk_inline"},
                None,
            )
        MockClient.assert_called_once_with(host="www.voog.com", api_token="vk_inline")

    def test_custom_host(self):
        # Real-world override: a tenant on their own primary domain
        # (Stella uses stellasoomlais.com). `tenant.example.com` is
        # NOT accepted — `.example` is a reserved/private TLD per
        # SSRF defense.
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk", "host": "stellasoomlais.com"},
                None,
            )
        MockClient.assert_called_once_with(host="stellasoomlais.com", api_token="vk")

    def test_token_env_missing_in_env(self):
        with patch.dict("os.environ", {}, clear=True):
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token_env": "VOOG_NOT_SET"},
                None,
            )
        self.assertTrue(result.isError)
        # Distinguishable from "set but empty" — error message must
        # mention "not set" not "empty".
        msg = result.content[0].text
        self.assertIn("not set", msg)

    def test_token_env_set_but_empty(self):
        # Common operator footgun: `VOOG_API_KEY=` in .env (truncated
        # paste, accidental newline-trim). Must surface a distinct
        # error from "unset".
        with patch.dict("os.environ", {"VOOG_EMPTY": ""}, clear=False):
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token_env": "VOOG_EMPTY"},
                None,
            )
        self.assertTrue(result.isError)
        msg = result.content[0].text
        self.assertIn("empty", msg.lower())

    def test_token_env_whitespace_only(self):
        # Same class of footgun — whitespace pasted in as the value.
        with patch.dict("os.environ", {"VOOG_WS": "   "}, clear=False):
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token_env": "VOOG_WS"},
                None,
            )
        self.assertTrue(result.isError)
        msg = result.content[0].text
        self.assertIn("empty", msg.lower())

    def test_neither_token_nor_token_env(self):
        result = mt.call_tool("voog_list_my_sites", {}, None)
        self.assertTrue(result.isError)

    def test_both_token_and_token_env_rejected(self):
        result = mt.call_tool(
            "voog_list_my_sites",
            {"token": "x", "token_env": "Y"},
            None,
        )
        self.assertTrue(result.isError)

    def test_r6_honesty_in_summary(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = [
                {"name": "alpha", "primary_domain": "alpha.com", "feature_flags": []}
            ]
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk"},
                None,
            )
        summary_text = result[0].text
        self.assertIn("site-scoped", summary_text)

    def test_unexpected_response_shape(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = {"not": "a list"}
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk"},
                None,
            )
        self.assertTrue(result.isError)

    def test_dispatcher_passes_none_client(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            mt.call_tool("voog_list_my_sites", {"token": "x"}, None)


class TestHostSSRFDefense(unittest.TestCase):
    """Voog tokens have full admin scope. A prompt-injected
    voog_list_my_sites(host=attacker, token_env=VOOG_API_KEY) call
    ships the token to a third party as `X-API-Token: <secret>`.
    These tests verify the SSRF-defensive host validator rejects
    LLM-controllable inputs an attacker could weaponise.
    """

    def _assert_rejected(self, host):
        # No mock — validation runs before VoogClient is constructed.
        result = mt.call_tool(
            "voog_list_my_sites",
            {"token": "vk", "host": host},
            None,
        )
        self.assertTrue(
            result.isError,
            f"host {host!r} should be rejected by SSRF defense",
        )

    def test_rejects_localhost(self):
        self._assert_rejected("localhost")

    def test_rejects_raw_ipv4_loopback(self):
        self._assert_rejected("127.0.0.1")

    def test_rejects_raw_ipv4_rfc1918(self):
        self._assert_rejected("192.168.1.1")
        self._assert_rejected("10.0.0.1")
        self._assert_rejected("172.16.0.1")

    def test_rejects_raw_ipv4_link_local(self):
        self._assert_rejected("169.254.169.254")  # AWS metadata endpoint

    def test_rejects_raw_ipv6(self):
        self._assert_rejected("::1")
        self._assert_rejected("fe80::1")

    def test_rejects_embedded_port(self):
        self._assert_rejected("evil.com:8080")
        self._assert_rejected("www.voog.com:80")  # downgrade attempt

    def test_rejects_url_scheme(self):
        self._assert_rejected("http://evil.com")
        self._assert_rejected("https://evil.com")
        self._assert_rejected("file:///etc/passwd")

    def test_rejects_userinfo(self):
        self._assert_rejected("user:pass@evil.com")

    def test_rejects_path_segment(self):
        self._assert_rejected("voog.com/admin")
        self._assert_rejected("voog.com?x=1")

    def test_rejects_private_tlds(self):
        self._assert_rejected("router.local")
        self._assert_rejected("evil.onion")
        self._assert_rejected("staging.internal")
        self._assert_rejected("tenant.example.com")  # .example is reserved
        self._assert_rejected("foo.test")
        self._assert_rejected("foo.invalid")

    def test_rejects_idn_homograph_attack(self):
        # Unicode-confusable a (Cyrillic а U+0430) in voog.com
        self._assert_rejected("vоog.com")

    def test_accepts_default_host(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            result = mt.call_tool("voog_list_my_sites", {"token": "vk"}, None)
        self.assertFalse(getattr(result, "isError", False))

    def test_accepts_voog_subdomain(self):
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk", "host": "helloworld.voog.com"},
                None,
            )
        self.assertFalse(getattr(result, "isError", False))

    def test_accepts_tenant_primary_domain(self):
        # Real-world: Stella's admin API lives on stellasoomlais.com.
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk", "host": "stellasoomlais.com"},
                None,
            )
        self.assertFalse(getattr(result, "isError", False))


class TestServerToolRegistry(unittest.TestCase):
    def test_me_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(mt, server.TOOL_GROUPS)

    def test_voog_list_my_sites_in_no_site_allowlist(self):
        from voog.mcp import server

        self.assertIn("voog_list_my_sites", server._BUILTIN_NO_SITE_TOOLS)
