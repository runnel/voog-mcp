"""Tests for voog.mcp.tools.products."""

import json
import unittest
import urllib.error
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
        # v1.2: fields is optional (back-compat), attributes and translations added
        self.assertEqual(schema["properties"]["fields"]["type"], "object")
        self.assertIn("attributes", schema["properties"])
        self.assertIn("translations", schema["properties"])
        # Only site + product_id are required; fields/attributes/translations are optional
        self.assertIn("product_id", schema["required"])
        self.assertNotIn("fields", schema["required"])

    def test_read_only_tools_have_full_explicit_annotations(self):
        # products_list and product_get must have the full triple
        # (readOnlyHint=True, destructiveHint=False, idempotentHint=True)
        # per PR #27/#28 always-explicit pattern. Read-only + idempotent
        # because repeated reads return the same data.
        tools = {t.name: t for t in products_tools.get_tools()}
        for name in ("products_list", "product_get"):
            ann = tools[name].annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"),
                True,
                f"{name} must have readOnlyHint=True",
            )
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"),
                False,
                f"{name} should set destructiveHint=False explicitly",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"),
                True,
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
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get_all.return_value = [
            {"id": 1, "name": "Widget", "slug": "widget", "status": "live"},
        ]
        result = products_tools.call_tool(
            "products_list",
            {},
            client,
        )
        client.get_all.assert_called_once_with(
            "/products",
            base="https://example.com/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )
        # success_response with summary → 2 TextContents (summary + JSON)
        self.assertEqual(len(result), 2)

    def test_products_list_simplified_projection(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
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
            "products_list",
            {},
            client,
        )
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        item = items[0]
        # Curated fields kept
        for keep in (
            "id",
            "name",
            "slug",
            "sku",
            "status",
            "in_stock",
            "on_sale",
            "price",
            "effective_price",
            "translations",
            "updated_at",
        ):
            self.assertIn(keep, item)
        # Heavier fields stripped (consistent with voog.mcp.resources.products)
        self.assertNotIn("description", item)
        self.assertNotIn("physical_properties", item)

    def test_products_list_empty(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get_all.return_value = []
        result = products_tools.call_tool(
            "products_list",
            {},
            client,
        )
        items = json.loads(result[1].text)
        self.assertEqual(items, [])

    def test_products_list_api_error(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = products_tools.call_tool(
            "products_list",
            {},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("products_list", payload["error"])


class TestProductGet(unittest.TestCase):
    def test_product_get_uses_ecommerce_base_with_both_includes(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = {
            "id": 42,
            "name": "X",
            "slug": "x",
            "translations": {},
            "variant_types": [],
        }
        result = products_tools.call_tool(
            "product_get",
            {"product_id": 42},
            client,
        )
        client.get.assert_called_once_with(
            "/products/42",
            base="https://example.com/admin/api/ecommerce/v1",
            params={"include": "variant_types,translations"},
        )
        # Detail returned in full (no projection)
        self.assertEqual(len(result), 1)  # success_response without summary

    def test_product_get_api_error(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        result = products_tools.call_tool(
            "product_get",
            {"product_id": 999},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("product_get", payload["error"])


class TestProductUpdate(unittest.TestCase):
    def test_simple_translation_field_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
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
            base="https://example.com/admin/api/ecommerce/v1",
        )
        self.assertEqual(len(result), 2)

    def test_multilingual_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "fields": {
                    "name-et": "Eesti",
                    "name-en": "English",
                    "slug-et": "eesti",
                    "slug-en": "english",
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
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
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
        products_tools.call_tool(
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
    surfaces import :func:`simplify_products` from :mod:`voog.projections`,
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

        sample = [
            {
                "id": 1,
                "name": "X",
                "slug": "x",
                "sku": "X-1",
                "status": "live",
                "in_stock": True,
                "on_sale": False,
                "price": "1900",
                "effective_price": "1900",
                "translations": {"name": {"et": "Iks"}},
                "updated_at": "2026-01-01T00:00:00Z",
                "description": "this MUST be stripped",
                "physical_properties": {"weight": 100},
                "asset_ids": [1, 2, 3],
            }
        ]
        out = simplify_products(sample)
        self.assertNotIn("description", out[0])
        self.assertNotIn("physical_properties", out[0])
        self.assertNotIn("asset_ids", out[0])


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = MagicMock()
        result = products_tools.call_tool(
            "nonexistent",
            {},
            client,
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

        all_names = [tool.name for group in server.TOOL_GROUPS for tool in group.get_tools()]
        self.assertEqual(len(all_names), len(set(all_names)), f"Duplicate tool names: {all_names}")


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import products as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


class TestProductUpdateExpandedFields(unittest.TestCase):
    """v1.2: product_update accepts the full {product: {...}} envelope.

    Backwards-compatible with the v1.1 'fields' translation-only shape:
    if `fields` is present, it still routes to translations. New
    parameters `attributes` and `translations` carry the rest.
    """

    def _put_call(self, client):
        return client.put.call_args.args[1]["product"]

    def test_description_translation_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "translations": {"description": {"et": "Eesti tekst"}},
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["translations"]["description"]["et"], "Eesti tekst")

    def test_status_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"status": "live"}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["status"], "live")

    def test_status_invalid_rejected(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"status": "active"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("status", payload["error"].lower())
        client.put.assert_not_called()

    def test_price_and_sku_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {
                    "price": "39.00",
                    "sale_price": "29.00",
                    "sku": "BAG-001",
                    "stock": 10,
                },
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["price"], "39.00")
        self.assertEqual(body["sale_price"], "29.00")
        self.assertEqual(body["sku"], "BAG-001")
        self.assertEqual(body["stock"], 10)

    def test_categories_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"category_ids": [1, 7]}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["category_ids"], [1, 7])

    def test_back_compat_fields_param(self):
        # Old shape still works (fields → translations).
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name-et": "Suvekott"}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["translations"]["name"]["et"], "Suvekott")

    def test_combined_attributes_and_translations(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {"status": "draft", "price": "49.00"},
                "translations": {
                    "description": {"et": "ET", "en": "EN"},
                    "name": {"et": "Nimi"},
                },
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["price"], "49.00")
        self.assertEqual(body["translations"]["description"]["et"], "ET")
        self.assertEqual(body["translations"]["name"]["et"], "Nimi")

    def test_rejects_empty_call(self):
        # No fields, no attributes, no translations.
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_rejects_unknown_attribute(self):
        # Defensive: catch typos like 'descriptin' before they hit the API.
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"descriptin": "oops"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("descriptin", payload["error"])
        client.put.assert_not_called()


class TestProductUpdateDestructiveDefaults(unittest.TestCase):
    """Three known Voog gotchas reachable via the v1.2 envelope expansion.

    See PR #90 follow-up review:
      - asset_ids on PUT silently keeps only hero (PUT envelope is
        assets:[{id}], not asset_ids — POST-only).
      - variants without variant_attributes wipes ALL variants even
        ones with id.
      - attributes ∩ translations field overlap produces undefined
        behaviour (description in both at once).
    """

    def _put_call(self, client):
        return client.put.call_args.args[1]["product"]

    # --- Fix A: asset_ids -> assets:[{id}] translation on PUT ---

    def test_product_update_translates_asset_ids_to_assets_on_put(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"asset_ids": [1, 2, 3]}},
            client,
        )
        body = self._put_call(client)
        # Translated to PUT envelope shape
        self.assertEqual(body.get("assets"), [{"id": 1}, {"id": 2}, {"id": 3}])
        # Raw asset_ids must NOT survive into the PUT body — Voog silently
        # keeps only the first/hero image when it sees asset_ids on PUT.
        self.assertNotIn("asset_ids", body)

    # --- Fix B: variants requires variant_attributes (or force=true) ---

    def test_product_update_rejects_variants_without_variant_attributes_unless_forced(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        # Without force: rejected
        result = products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {"variants": [{"id": 7, "stock": 3}]},
            },
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("variant_attributes", payload["error"])
        client.put.assert_not_called()

        # With force=true: passes through
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {"variants": [{"id": 7, "stock": 3}]},
                "force": True,
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["variants"], [{"id": 7, "stock": 3}])
        # force is internal to the tool, not part of the envelope
        self.assertNotIn("force", body)

    def test_product_update_accepts_variants_with_variant_attributes(self):
        # Regression guard: providing both alongside is the safe path.
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        result = products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {
                    "variants": [{"id": 7, "stock": 3}],
                    "variant_attributes": [{"id": 1, "name": "size"}],
                },
            },
            client,
        )
        # No error, PUT happened
        self.assertEqual(len(result), 2)
        body = self._put_call(client)
        self.assertEqual(body["variants"], [{"id": 7, "stock": 3}])
        self.assertEqual(body["variant_attributes"], [{"id": 1, "name": "size"}])

    # --- Fix C: attributes ∩ translations field overlap rejected ---

    def test_product_update_rejects_attributes_translations_field_overlap(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {"description": "X"},
                "translations": {"description": {"et": "Y"}},
            },
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("description", payload["error"])
        client.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
