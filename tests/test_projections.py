"""Tests for voog.projections — shared simplify_* projections.

These are the canonical projections used by both the tools surface and the
resources surface. They live in their own module so the two surfaces can't
silently drift apart (review fix: prompt 6).
"""
import unittest

from voog.projections import (
    simplify_articles,
    simplify_layouts,
    simplify_pages,
    simplify_products,
)


class TestSimplifyPages(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(simplify_pages([]), [])

    def test_full_page(self):
        page = {
            "id": 1,
            "path": "/about",
            "title": "About",
            "hidden": False,
            "layout_id": 10,
            "layout_name": "Default",
            "content_type": "common_page",
            "parent_id": None,
            "language": {"code": "et"},
            "public_url": "https://example.com/about",
        }
        result = simplify_pages([page])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {
            "id": 1,
            "path": "/about",
            "title": "About",
            "hidden": False,
            "layout_id": 10,
            "layout_name": "Default",
            "content_type": "common_page",
            "parent_id": None,
            "language_code": "et",
            "public_url": "https://example.com/about",
        })

    def test_missing_fields_graceful(self):
        # Missing optional fields and nested dicts → output uses .get() defaults.
        result = simplify_pages([{"id": 5}])
        self.assertEqual(result[0]["id"], 5)
        self.assertIsNone(result[0]["title"])
        self.assertIsNone(result[0]["language_code"])
        self.assertIsNone(result[0]["layout_id"])
        self.assertIsNone(result[0]["layout_name"])

    def test_extra_fields_stripped(self):
        page = {
            "id": 7,
            "title": "T",
            "huge_body": "x" * 1000,
            "internal_secret": "shh",
        }
        result = simplify_pages([page])
        self.assertNotIn("huge_body", result[0])
        self.assertNotIn("internal_secret", result[0])

    def test_layout_nested_fallback(self):
        # When top-level layout_id/layout_name are missing, fall back to nested layout dict.
        page = {"id": 9, "layout": {"id": 22, "title": "Nested"}}
        result = simplify_pages([page])
        self.assertEqual(result[0]["layout_id"], 22)
        self.assertEqual(result[0]["layout_name"], "Nested")

    def test_layout_top_level_wins(self):
        # Top-level layout_id/layout_name take precedence over nested layout.
        page = {
            "id": 9,
            "layout_id": 1,
            "layout_name": "Top",
            "layout": {"id": 22, "title": "Nested"},
        }
        result = simplify_pages([page])
        self.assertEqual(result[0]["layout_id"], 1)
        self.assertEqual(result[0]["layout_name"], "Top")


class TestSimplifyProducts(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(simplify_products([]), [])

    def test_full_product(self):
        product = {
            "id": 100,
            "name": "Bag",
            "slug": "bag",
            "sku": "BAG-1",
            "status": "active",
            "in_stock": True,
            "on_sale": False,
            "price": "100.00",
            "effective_price": "100.00",
            "translations": {"name": {"et": "Kott"}},
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = simplify_products([product])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], product)

    def test_missing_fields_graceful(self):
        result = simplify_products([{"id": 1}])
        self.assertEqual(result[0]["id"], 1)
        self.assertIsNone(result[0]["status"])
        self.assertIsNone(result[0]["price"])
        self.assertIsNone(result[0]["translations"])

    def test_extra_fields_stripped(self):
        # Description/asset_ids/physical_properties are intentionally stripped
        # — clients fetching voog://products/{id} get the full detail.
        product = {
            "id": 2,
            "name": "X",
            "description": "huge HTML body",
            "asset_ids": [1, 2, 3],
            "physical_properties": {"weight": 500},
        }
        result = simplify_products([product])
        self.assertNotIn("description", result[0])
        self.assertNotIn("asset_ids", result[0])
        self.assertNotIn("physical_properties", result[0])


class TestSimplifyArticles(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(simplify_articles([]), [])

    def test_full_article(self):
        article = {
            "id": 50,
            "title": "Hello",
            "path": "/blog/hello",
            "public_url": "https://example.com/blog/hello",
            "published": True,
            "published_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "created_at": "2025-12-31T00:00:00Z",
            "language": {"code": "et"},
            "page": {"id": 11},
            "body": "should be stripped from list view",
        }
        result = simplify_articles([article])
        self.assertEqual(result[0]["id"], 50)
        self.assertEqual(result[0]["language_code"], "et")
        self.assertEqual(result[0]["page_id"], 11)
        self.assertNotIn("body", result[0])

    def test_missing_nested_dicts(self):
        result = simplify_articles([{"id": 1}])
        self.assertIsNone(result[0]["language_code"])
        self.assertIsNone(result[0]["page_id"])


class TestSimplifyLayouts(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(simplify_layouts([]), [])

    def test_full_layout(self):
        layout = {
            "id": 7,
            "title": "Home",
            "component": "page",
            "content_type": "common_page",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "{% liquid %}",
        }
        result = simplify_layouts([layout])
        self.assertEqual(result[0]["id"], 7)
        self.assertEqual(result[0]["title"], "Home")
        self.assertNotIn("body", result[0])

    def test_missing_fields_graceful(self):
        result = simplify_layouts([{"id": 3}])
        self.assertEqual(result[0]["id"], 3)
        self.assertIsNone(result[0]["title"])


if __name__ == "__main__":
    unittest.main()
