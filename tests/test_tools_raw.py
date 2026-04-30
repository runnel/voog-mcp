"""Tests for voog.mcp.tools.raw — generic Admin/Ecommerce passthrough."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from voog.mcp.tools import raw as raw_tools


class TestGetTools(unittest.TestCase):
    def test_two_tools_registered(self):
        names = [t.name for t in raw_tools.get_tools()]
        self.assertEqual(
            sorted(names),
            ["voog_admin_api_call", "voog_ecommerce_api_call"],
        )

    def test_admin_call_annotations(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        ann = tools["voog_admin_api_call"].annotations
        # Generic passthrough — any method possible, so the annotations
        # must be conservative.
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestAdminApiCall(unittest.TestCase):
    def test_get_request_passthrough(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = [{"id": 1}, {"id": 2}]
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/forms"},
            client,
        )
        client.get.assert_called_once_with(
            "/forms",
            base="https://example.com/admin/api",
            params=None,
        )
        body = json.loads(result[1].text)
        self.assertEqual(body, [{"id": 1}, {"id": 2}])

    def test_get_with_params(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = {"ok": True}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "GET",
                "path": "/articles",
                "params": {"q.article.title.$cont": "kuju"},
            },
            client,
        )
        client.get.assert_called_once_with(
            "/articles",
            base="https://example.com/admin/api",
            params={"q.article.title.$cont": "kuju"},
        )

    def test_put_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.put.return_value = {"id": 42, "title": "X"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PUT",
                "path": "/forms/42",
                "body": {"title": "X"},
            },
            client,
        )
        client.put.assert_called_once_with(
            "/forms/42",
            {"title": "X"},
            base="https://example.com/admin/api",
        )

    def test_post_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.post.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "POST",
                "path": "/articles",
                "body": {"page_id": 1, "autosaved_title": "Draft"},
            },
            client,
        )
        client.post.assert_called_once_with(
            "/articles",
            {"page_id": 1, "autosaved_title": "Draft"},
            base="https://example.com/admin/api",
        )

    def test_delete_request(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.delete.return_value = None
        raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/redirect_rules/9"},
            client,
        )
        client.delete.assert_called_once_with(
            "/redirect_rules/9",
            base="https://example.com/admin/api",
            params=None,
        )

    def test_patch_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.patch.return_value = {"id": 5, "title": "X"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PATCH",
                "path": "/site",
                "body": {"title": "X"},
            },
            client,
        )
        client.patch.assert_called_once_with(
            "/site",
            {"title": "X"},
            base="https://example.com/admin/api",
        )

    def test_rejects_percent_encoded_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/%2e%2e/%2e%2e/etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)

    def test_rejects_unknown_method(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "TRACE", "path": "/site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("method", payload["error"].lower())

    def test_rejects_path_without_leading_slash(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("/", payload["error"])

    def test_rejects_absolute_url(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "https://evil.example.com/exfil"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute", payload["error"].lower())

    def test_rejects_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/../../../../etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)

    def test_api_error_propagates(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.side_effect = urllib.error.HTTPError(
            "url", 422, "Unprocessable Entity", {}, None
        )
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("422", payload["error"])


class TestEcommerceApiCall(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = []
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {"method": "GET", "path": "/orders"},
            client,
        )
        client.get.assert_called_once_with(
            "/orders",
            base="https://example.com/admin/api/ecommerce/v1",
            params=None,
        )

    def test_put_settings(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"settings": {}}
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {
                "method": "PUT",
                "path": "/settings",
                "body": {
                    "settings": {
                        "translations": {
                            "products_url_slug": {"en": "products"}
                        }
                    }
                },
            },
            client,
        )
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )
