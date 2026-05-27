"""Tests for voog.mcp.tools.categories."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog.mcp.tools import categories as ct

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ecommerce"


def _load_fixture(name: str):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)["response"]


def _make_client():
    client = MagicMock()
    client.ecommerce_url = "https://stella.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_five_tools_registered(self):
        names = sorted(t.name for t in ct.get_tools())
        self.assertEqual(
            names,
            [
                "categories_list",
                "category_create",
                "category_delete",
                "category_get",
                "category_update",
            ],
        )


class TestCategoriesList(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("categories_list")
        ct.call_tool("categories_list", {}, client)
        client.get_all.assert_called_once_with("/categories", base=client.ecommerce_url)

    def test_returns_count_summary(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("categories_list")
        result = ct.call_tool("categories_list", {}, client)
        self.assertFalse(getattr(result, "isError", False))

    def test_annotations(self):
        ann = {t.name: t for t in ct.get_tools()}["categories_list"].annotations
        self.assertIs(ann.readOnlyHint, True)


class TestCategoryGet(unittest.TestCase):
    def test_fetches_by_id(self):
        client = _make_client()
        client.get.return_value = _load_fixture("category_get")
        ct.call_tool("category_get", {"category_id": 42}, client)
        client.get.assert_called_once_with("/categories/42", base=client.ecommerce_url)

    def test_category_id_bool_rejected(self):
        client = _make_client()
        result = ct.call_tool("category_get", {"category_id": True}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)


class TestCategoryCreate(unittest.TestCase):
    def test_requires_name(self):
        client = _make_client()
        result = ct.call_tool("category_create", {"slug": "x"}, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_envelope_shape(self):
        client = _make_client()
        client.post.return_value = _load_fixture("category_create")
        ct.call_tool("category_create", {"name": "Bags", "slug": "bags"}, client)
        args, kwargs = client.post.call_args
        path, body = args[0], args[1]
        self.assertEqual(path, "/categories")
        self.assertIn("category", body)
        self.assertEqual(body["category"]["name"], "Bags")
        self.assertEqual(body["category"]["slug"], "bags")
        self.assertIs(kwargs["base"], client.ecommerce_url)

    def test_parent_id_bool_rejected(self):
        client = _make_client()
        result = ct.call_tool("category_create", {"name": "X", "parent_id": True}, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)


class TestCategoryUpdate(unittest.TestCase):
    def test_requires_at_least_one_field(self):
        client = _make_client()
        result = ct.call_tool("category_update", {"category_id": 42}, client)
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_partial_envelope(self):
        client = _make_client()
        client.put.return_value = _load_fixture("category_update")
        ct.call_tool("category_update", {"category_id": 42, "name": "Renamed"}, client)
        args, _kwargs = client.put.call_args
        self.assertEqual(args[0], "/categories/42")
        self.assertEqual(args[1], {"category": {"name": "Renamed"}})


class TestCategoryDelete(unittest.TestCase):
    def test_requires_force(self):
        client = _make_client()
        result = ct.call_tool("category_delete", {"category_id": 42}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = _make_client()
        client.delete.return_value = None
        ct.call_tool("category_delete", {"category_id": 42, "force": True}, client)
        client.delete.assert_called_once_with("/categories/42", base=client.ecommerce_url)

    def test_annotations(self):
        ann = {t.name: t for t in ct.get_tools()}["category_delete"].annotations
        self.assertIs(ann.destructiveHint, True)


class TestServerToolRegistry(unittest.TestCase):
    def test_categories_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(ct, server.TOOL_GROUPS)


if __name__ == "__main__":
    unittest.main()
