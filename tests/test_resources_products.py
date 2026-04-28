"""Tests for voog_mcp.resources.products."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.resources import products as products_resources


class TestProductsResourcesGetResources(unittest.TestCase):
    def test_get_resources_returns_listable_root(self):
        resources = products_resources.get_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(str(resources[0].uri), "voog://products")
        self.assertEqual(resources[0].mimeType, "application/json")
        self.assertTrue(resources[0].name)
        self.assertTrue(resources[0].description)


class TestProductsResourcesMatches(unittest.TestCase):
    def test_matches_root_uri(self):
        self.assertTrue(products_resources.matches("voog://products"))

    def test_matches_single_product_uri(self):
        self.assertTrue(products_resources.matches("voog://products/42"))

    def test_does_not_match_other_groups(self):
        self.assertFalse(products_resources.matches("voog://pages"))
        self.assertFalse(products_resources.matches("voog://articles"))
        self.assertFalse(products_resources.matches("voog://layouts"))
        self.assertFalse(products_resources.matches("voog://redirects"))

    def test_does_not_match_prefix_lookalike(self):
        self.assertFalse(products_resources.matches("voog://productsx"))
        self.assertFalse(products_resources.matches("voog://products-old"))

    def test_does_not_match_empty(self):
        self.assertFalse(products_resources.matches(""))
        self.assertFalse(products_resources.matches("voog://"))


class TestProductsResourcesReadRoot(unittest.TestCase):
    def test_read_root_uses_ecommerce_base_with_translations_include(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = [
            {
                "id": 42,
                "name": "T-shirt",
                "slug": "t-shirt",
                "sku": "TS-01",
                "status": "active",
                "in_stock": True,
                "on_sale": False,
                "price": "1900",
                "effective_price": "1900",
                "translations": {"name": {"et": "T-särk"}},
                "updated_at": "2026-04-01T00:00:00Z",
            },
        ]
        result = products_resources.read_resource("voog://products", client)

        # Must use the ecommerce base URL and request translations include
        client.get_all.assert_called_once_with(
            "/products",
            base="https://runnel.ee/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )
        client.get.assert_not_called()

        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item["id"], 42)
        self.assertEqual(item["name"], "T-shirt")
        self.assertEqual(item["slug"], "t-shirt")
        self.assertEqual(item["status"], "active")
        self.assertTrue(item["in_stock"])
        self.assertEqual(item["effective_price"], "1900")
        # translations preserved (small enough to inline; same shape voog.py CLI returns)
        self.assertIn("translations", item)

    def test_read_root_handles_missing_optional_fields(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = [
            {"id": 1, "name": "Bare"},  # most fields absent
        ]
        result = products_resources.read_resource("voog://products", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        item = parsed[0]
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["name"], "Bare")
        self.assertIsNone(item["status"])
        self.assertIsNone(item["effective_price"])

    def test_read_root_empty(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = []
        result = products_resources.read_resource("voog://products", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed, [])


class TestProductsResourcesReadSingleProduct(unittest.TestCase):
    def test_read_single_product_uses_ecommerce_base_with_both_includes(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        full_product = {
            "id": 42,
            "name": "T-shirt",
            "slug": "t-shirt",
            "translations": {"name": {"et": "T-särk"}},
            "variant_types": [{"id": 1, "name": "Size"}],
            "status": "active",
        }
        client.get.return_value = full_product

        result = products_resources.read_resource("voog://products/42", client)

        # Must request both includes per spec § 5
        client.get.assert_called_once_with(
            "/products/42",
            base="https://runnel.ee/admin/api/ecommerce/v1",
            params={"include": "variant_types,translations"},
        )
        client.get_all.assert_not_called()

        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        # Single product detail returned in full (not simplified) — caller asked for the URI
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed["id"], 42)
        self.assertIn("translations", parsed)
        self.assertIn("variant_types", parsed)

    def test_read_single_product_works_when_variant_types_absent(self):
        # Voog only returns variant_types when product uses variants.
        # Resource MUST NOT crash when API omits the field.
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get.return_value = {
            "id": 42,
            "name": "Plain",
            "translations": {},
            # no variant_types key
        }
        result = products_resources.read_resource("voog://products/42", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed["id"], 42)
        self.assertNotIn("variant_types", parsed)

    def test_read_single_product_rejects_non_integer_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            products_resources.read_resource("voog://products/abc", client)
        client.get.assert_not_called()

    def test_read_single_product_rejects_zero_or_negative(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            products_resources.read_resource("voog://products/0", client)
        with self.assertRaises(ValueError):
            products_resources.read_resource("voog://products/-5", client)


class TestProductsResourcesUnknownUri(unittest.TestCase):
    def test_bare_trailing_slash_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            products_resources.read_resource("voog://products/", client)

    def test_subpath_rejected(self):
        # voog://products/{id}/variants is NOT a supported URI in v1
        client = MagicMock()
        with self.assertRaises(ValueError):
            products_resources.read_resource(
                "voog://products/42/variants", client
            )

    def test_completely_unrelated_uri_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            products_resources.read_resource("voog://pages", client)


class TestProductsResourcesErrorPropagation(unittest.TestCase):
    def test_root_propagates_api_errors(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.side_effect = urllib.error.URLError("network down")
        with self.assertRaises(urllib.error.URLError):
            products_resources.read_resource("voog://products", client)

    def test_single_product_propagates_api_errors(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        with self.assertRaises(urllib.error.HTTPError):
            products_resources.read_resource("voog://products/999", client)


class TestServerResourceRegistry(unittest.TestCase):
    """Phase D contract — products resources joined to RESOURCE_GROUPS."""

    def test_products_in_resource_groups(self):
        from voog_mcp import server
        self.assertIn(products_resources, server.RESOURCE_GROUPS)

    def test_no_uri_collisions_after_products_added(self):
        from voog_mcp import server
        all_uris = [
            str(r.uri)
            for g in server.RESOURCE_GROUPS
            for r in g.get_resources()
        ]
        self.assertEqual(len(all_uris), len(set(all_uris)),
                         f"Duplicate resource URIs: {all_uris}")

    def test_phase_d_complete(self):
        # Sentinel: after Task 18, RESOURCE_GROUPS should cover all 5 spec § 5 groups.
        from voog_mcp import server
        from voog_mcp.resources import (
            articles as articles_resources,
            layouts as layouts_resources,
            pages as pages_resources,
            products as products_resources_mod,
            redirects as redirects_resources,
        )
        expected = {
            articles_resources, layouts_resources, pages_resources,
            products_resources_mod, redirects_resources,
        }
        actual = set(server.RESOURCE_GROUPS)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
