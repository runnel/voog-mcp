"""Tests for voog.mcp.tools.site — admin /site singleton."""

import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import site as site_tools


class TestGetTools(unittest.TestCase):
    def test_four_tools(self):
        names = sorted(t.name for t in site_tools.get_tools())
        self.assertEqual(
            names,
            ["site_delete_data", "site_get", "site_set_data", "site_update"],
        )


class TestSiteGet(unittest.TestCase):
    def test_get(self):
        client = MagicMock()
        client.get.return_value = {"id": 1, "title": "Stella"}
        site_tools.call_tool("site_get", {}, client)
        client.get.assert_called_once_with("/site")


class TestSiteUpdate(unittest.TestCase):
    def test_update_title(self):
        client = MagicMock()
        client.put.return_value = {}
        site_tools.call_tool(
            "site_update",
            {"attributes": {"title": "New title"}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/site")
        self.assertEqual(body["title"], "New title")

    def test_update_rejects_code(self):
        # site.code is immutable per Voog (and project memory).
        client = MagicMock()
        result = site_tools.call_tool(
            "site_update",
            {"attributes": {"code": "newsubdomain"}},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestSiteSetData(unittest.TestCase):
    def test_set_data(self):
        client = MagicMock()
        client.put.return_value = {}
        site_tools.call_tool(
            "site_set_data",
            {"key": "buy_together", "value": {"products": []}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/site/data/buy_together")
        self.assertEqual(body, {"value": {"products": []}})

    def test_rejects_internal_prefix(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_set_data",
            {"key": "internal_x", "value": "y"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_set_data_rejects_slash_in_key(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_set_data",
            {"key": "foo/bar", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_set_data_rejects_question_mark_in_key(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_set_data",
            {"key": "foo?x=1", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_set_data_rejects_percent_encoded_traversal(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_set_data",
            {"key": "%2e%2e", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestSiteDeleteData(unittest.TestCase):
    def test_requires_force(self):
        # Without force=True, the call must be rejected and DELETE not called.
        client = MagicMock()
        result = site_tools.call_tool(
            "site_delete_data",
            {"key": "buy_together"},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_false_rejected(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_delete_data",
            {"key": "buy_together", "force": False},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        client = MagicMock()
        site_tools.call_tool(
            "site_delete_data",
            {"key": "buy_together", "force": True},
            client,
        )
        client.delete.assert_called_once_with("/site/data/buy_together")

    def test_rejects_internal_prefix(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_delete_data",
            {"key": "internal_x", "force": True},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()
