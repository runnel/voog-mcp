"""Tests for voog.mcp.tools.shipping (shipping_methods + gateways, read-only)."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog.mcp.tools import shipping as st

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ecommerce"


def _load_fixture(name: str):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)["response"]


def _make_client():
    client = MagicMock()
    client.ecommerce_url = "https://stella.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_two_tools_registered(self):
        names = sorted(t.name for t in st.get_tools())
        self.assertEqual(names, ["gateways_list", "shipping_methods_list"])


class TestShippingMethodsList(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("shipping_methods_list")
        st.call_tool("shipping_methods_list", {}, client)
        client.get_all.assert_called_once_with("/shipping_methods", base=client.ecommerce_url)

    def test_returns_count_summary(self):
        client = _make_client()
        rows = _load_fixture("shipping_methods_list")
        client.get_all.return_value = rows
        result = st.call_tool("shipping_methods_list", {}, client)
        self.assertFalse(getattr(result, "isError", False))
        # First text content is the summary; second is the JSON.
        self.assertIn(str(len(rows)), result[0].text)

    def test_annotations(self):
        ann = {t.name: t for t in st.get_tools()}["shipping_methods_list"].annotations
        self.assertIs(ann.readOnlyHint, True)


class TestGatewaysList(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("gateways_list")
        st.call_tool("gateways_list", {}, client)
        client.get_all.assert_called_once_with("/gateways", base=client.ecommerce_url)

    def test_annotations(self):
        ann = {t.name: t for t in st.get_tools()}["gateways_list"].annotations
        self.assertIs(ann.readOnlyHint, True)


class TestServerToolRegistry(unittest.TestCase):
    def test_shipping_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(st, server.TOOL_GROUPS)


if __name__ == "__main__":
    unittest.main()
