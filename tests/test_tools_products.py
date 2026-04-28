"""Tests for voog_mcp.tools.products."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock


from tests._test_helpers import _ann_get
from voog.mcp.tools import products as products_tools


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_three(self):
        tools = products_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["products_list", "product_get", "product_update"])

    def test_products_list_schema(self):
        tools = {t.name: t for t in products_tools.get_tools()}
        schema = tools["products_list"].inputSchema
        # Only 'site' required — list everything on the named site
        self.assertIn("site", schema["required"])

    def test_product_get_schema(self):
        tools = {t.name: t for t in products_tools.get_tools()}
        schema = tools["product_get"].inputSchema
        self.assertEqual(schema["properties"]["product_id"]["type"], "integer")
        self.assertIn("product_id", schema["required"])

    def test_product_update_schema(self):
        tools = {t.name: t for t in products_tools.get_tools()}
        schema = tools["product_update"].inputSchema
        self.assertEqual(schema["properties"]["product_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["fields"]["type"], "object")
        for req in ("product_id", "fields"):
            self.assertIn(req, schema["required"])

    def test_read_only_tools_have_full_explicit_annotations(self):
        # products_list and product_get must have the full triple
        # (readOnlyHint=True, destructiveHint=False, idempotentHint=True)
        # per PR #27/#28 always-explicit pattern. Read-only + idempotent
        # because repeated reads return the same data.
        tools = {t.name: t for t in products_tools.get_tools()}
        for name in ("products_list", "product_get"):
            ann = tools[name].annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"), True,
                f"{name} must have readOnlyHint=True",
            )
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"), False,
                f"{name} should set destructiveHint=False explicitly",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"), True,
                f"{name} should set idempotentHint=True (repeat reads = same data)",
            )

    def test_product_update_annotations(self):
        # Mutating but reversible (call again with old values to undo)
        # and idempotent (same fields twice = same end state)
        tools = {t.name: t for t in products_tools.get_tools()}
        ann = tools["product_update"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)


class TestProductsList(unittest.TestCase):
    def test_products_list_uses_ecommerce_base_with_translations(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = [
            {"id": 1, "name": "Widget", "slug": "widget", "status": "live"},
        ]
        result = products_tools.call_tool(
            "products_list", {}, client,
        )
        client.get_all.assert_called_once_with(
            "/products",
            base="https://runnel.ee/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )
        # success_response with summary → 2 TextContents (summary + JSON)
        self.assertEqual(len(result), 2)

    def test_products_list_simplified_projection(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = [
            {
                "id": 1,
                "name": "Widget",
                "slug": "widget",
                "sku": "WID-01",
                "status": "live",
                "in_stock": True,
                "on_sale": False,
                "price": "1900",
                "effective_price": "1900",
                "translations": {"name": {"et": "Vidin"}},
                "updated_at": "2026-04-20T00:00:00Z",
                "description": "should-not-leak",  # heavier field stripped
                "physical_properties": {"weight": 100},  # heavy field stripped
            },
        ]
        result = products_tools.call_tool(
            "products_list", {}, client,
        )
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        item = items[0]
        # Curated fields kept
        for keep in ("id", "name", "slug", "sku", "status", "in_stock", "on_sale",
                     "price", "effective_price", "translations", "updated_at"):
            self.assertIn(keep, item)
        # Heavier fields stripped (consistent with voog_mcp.resources.products)
        self.assertNotIn("description", item)
        self.assertNotIn("physical_properties", item)

    def test_products_list_empty(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.return_value = []
        result = products_tools.call_tool(
            "products_list", {}, client,
        )
        items = json.loads(result[1].text)
        self.assertEqual(items, [])

    def test_products_list_api_error(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = products_tools.call_tool(
            "products_list", {}, client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("products_list", payload["error"])


class TestProductGet(unittest.TestCase):
    def test_product_get_uses_ecommerce_base_with_both_includes(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get.return_value = {
            "id": 42, "name": "X", "slug": "x", "translations": {}, "variant_types": [],
        }
        result = products_tools.call_tool(
            "product_get", {"product_id": 42}, client,
        )
        client.get.assert_called_once_with(
            "/products/42",
            base="https://runnel.ee/admin/api/ecommerce/v1",
            params={"include": "variant_types,translations"},
        )
        # Detail returned in full (no projection)
        self.assertEqual(len(result), 1)  # success_response without summary

    def test_product_get_api_error(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.get.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        result = products_tools.call_tool(
            "product_get", {"product_id": 999}, client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("product_get", payload["error"])


class TestProductUpdate(unittest.TestCase):
    def test_simple_translation_field_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42, "name": "Updated"}
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name-et": "Eesti", "slug-et": "eesti"}},
            client,
        )
        # Should PUT to ecommerce base with nested translations payload
        client.put.assert_called_once_with(
            "/products/42",
            {
                "product": {
                    "translations": {
                        "name": {"et": "Eesti"},
                        "slug": {"et": "eesti"},
                    }
                }
            },
            base="https://runnel.ee/admin/api/ecommerce/v1",
        )
        self.assertEqual(len(result), 2)

    def test_multilingual_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "fields": {
                    "name-et": "Eesti", "name-en": "English",
                    "slug-et": "eesti", "slug-en": "english",
                },
            },
            client,
        )
        args, kwargs = client.put.call_args
        translations = args[1]["product"]["translations"]
        self.assertEqual(translations["name"], {"et": "Eesti", "en": "English"})
        self.assertEqual(translations["slug"], {"et": "eesti", "en": "english"})

    def test_unknown_field_rejected(self):
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"price-et": "100"}},  # 'price' not allowed
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("price", payload["error"])

    def test_field_without_lang_suffix_rejected(self):
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name": "missing-lang"}},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_empty_fields_rejected(self):
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {}},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_api_error_returns_error_response(self):
        client = MagicMock()
        client.ecommerce_url = "https://runnel.ee/admin/api/ecommerce/v1"
        client.put.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 999, "fields": {"name-et": "X"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("product_update", payload["error"])

    def test_empty_lang_segment_rejected(self):
        # 'name-' splits to lang='' — Voog would reject with a generic 422
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name-": "X"}},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("lang segment", payload["error"])

    def test_double_dash_lang_rejected(self):
        # 'name--et' splits to lang='-et' (starts with '-'); Voog would reject
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name--et": "X"}},
            client,
        )
        client.put.assert_not_called()

    def test_empty_value_rejected(self):
        # Voog rejects empty translations; we surface this earlier with a
        # precise error rather than letting the API speak generically
        client = MagicMock()
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name-et": ""}},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("empty value", payload["error"])


class TestProjectionConsistency(unittest.TestCase):
    """Critical cross-module invariant per PR #30 review.

    The PR description claims products_list tool and voog://products resource
    produce identical shapes. The shape is now enforced structurally: both
    surfaces import :func:`simplify_products` from :mod:`voog_mcp.projections`,
    so the two cannot drift. This test pins the import to that single source
    of truth — if anyone re-introduces a local copy in either module, the
    assertion fails.
    """

    def test_both_surfaces_use_shared_projection(self):
        from voog import projections
        from voog.mcp.resources import products as resource_products
        self.assertIs(products_tools.simplify_products, projections.simplify_products)
        self.assertIs(resource_products.simplify_products, projections.simplify_products)

    def test_shared_projection_strips_heavy_fields(self):
        # Sample with both retained and stripped fields. The heavy fields
        # (description, physical_properties, asset_ids) must be stripped from
        # the list view — clients fetching voog://products/{id} get the full
        # detail.
        from voog.projections import simplify_products
        sample = [{
            "id": 1, "name": "X", "slug": "x", "sku": "X-1",
            "status": "live", "in_stock": True, "on_sale": False,
            "price": "1900", "effective_price": "1900",
            "translations": {"name": {"et": "Iks"}},
            "updated_at": "2026-01-01T00:00:00Z",
            "description": "this MUST be stripped",
            "physical_properties": {"weight": 100},
            "asset_ids": [1, 2, 3],
        }]
        out = simplify_products(sample)
        self.assertNotIn("description", out[0])
        self.assertNotIn("physical_properties", out[0])
        self.assertNotIn("asset_ids", out[0])


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = MagicMock()
        result = products_tools.call_tool(
            "nonexistent", {}, client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — products_tools joined to TOOL_GROUPS."""

    def test_products_tools_in_tool_groups(self):
        from voog.mcp import server
        self.assertIn(products_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        from voog.mcp import server
        all_names = [
            tool.name
            for group in server.TOOL_GROUPS
            for tool in group.get_tools()
        ]
        self.assertEqual(len(all_names), len(set(all_names)),
                         f"Duplicate tool names: {all_names}")


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import products as mod
        for tool in mod.get_tools():
            self.assertIn("site", tool.inputSchema.get("required", []),
                          f"tool {tool.name} must require 'site'")


if __name__ == "__main__":
    unittest.main()
