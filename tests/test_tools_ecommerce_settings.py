"""Tests for voog.mcp.tools.ecommerce_settings."""

import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import ecommerce_settings as es


class TestGetTools(unittest.TestCase):
    def test_two_tools(self):
        names = sorted(t.name for t in es.get_tools())
        self.assertEqual(
            names,
            ["ecommerce_settings_get", "ecommerce_settings_update"],
        )


class TestGet(unittest.TestCase):
    def test_get_with_translations_include(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = {"settings": {}}
        es.call_tool("ecommerce_settings_get", {}, client)
        client.get.assert_called_once_with(
            "/settings",
            base="https://example.com/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )


class TestUpdate(unittest.TestCase):
    def test_update_currency_attr(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {"attributes": {"currency": "EUR"}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(body["settings"]["currency"], "EUR")

    def test_update_products_url_slug_translations(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {
                "translations": {
                    "products_url_slug": {"en": "products"},
                }
            },
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )

    def test_rejects_empty_call(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = es.call_tool("ecommerce_settings_update", {}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()
