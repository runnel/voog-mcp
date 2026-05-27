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
        with patch("voog.mcp.tools.me.VoogClient") as MockClient:
            MockClient.return_value.get.return_value = []
            mt.call_tool(
                "voog_list_my_sites",
                {"token": "vk", "host": "tenant.example.com"},
                None,
            )
        MockClient.assert_called_once_with(host="tenant.example.com", api_token="vk")

    def test_token_env_missing_in_env(self):
        with patch.dict("os.environ", {}, clear=True):
            result = mt.call_tool(
                "voog_list_my_sites",
                {"token_env": "VOOG_NOT_SET"},
                None,
            )
        self.assertTrue(result.isError)

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


class TestServerToolRegistry(unittest.TestCase):
    def test_me_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(mt, server.TOOL_GROUPS)

    def test_voog_list_my_sites_in_no_site_allowlist(self):
        from voog.mcp import server

        self.assertIn("voog_list_my_sites", server._BUILTIN_NO_SITE_TOOLS)
