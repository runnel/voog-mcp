"""Tests for voog_mcp.resources.layouts."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from voog.mcp.resources import layouts as layouts_resources


class TestLayoutsResourcesGetResources(unittest.TestCase):
    def test_get_resources_returns_listable_root(self):
        resources = layouts_resources.get_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(str(resources[0].uri), "voog://{site}/layouts")
        self.assertEqual(resources[0].mimeType, "application/json")
        self.assertTrue(resources[0].name)
        self.assertTrue(resources[0].description)


class TestLayoutsResourcesMatches(unittest.TestCase):
    def test_matches_root_uri(self):
        self.assertTrue(layouts_resources.matches("voog://stella/layouts"))

    def test_matches_single_layout_uri(self):
        self.assertTrue(layouts_resources.matches("voog://stella/layouts/977702"))

    def test_matches_any_site(self):
        self.assertTrue(layouts_resources.matches("voog://runnel/layouts"))

    def test_does_not_match_other_groups(self):
        self.assertFalse(layouts_resources.matches("voog://stella/pages"))
        self.assertFalse(layouts_resources.matches("voog://stella/articles"))
        self.assertFalse(layouts_resources.matches("voog://stella/redirects"))

    def test_does_not_match_prefix_lookalike(self):
        self.assertFalse(layouts_resources.matches("voog://stella/layoutsx"))
        self.assertFalse(layouts_resources.matches("voog://stella/layouts-old"))

    def test_does_not_match_empty(self):
        self.assertFalse(layouts_resources.matches(""))
        self.assertFalse(layouts_resources.matches("voog://"))

    def test_does_not_match_legacy_format(self):
        self.assertFalse(layouts_resources.matches("voog://layouts"))
        self.assertFalse(layouts_resources.matches("voog://layouts/42"))


class TestLayoutsResourcesReadRoot(unittest.TestCase):
    def test_read_root_returns_simplified_layouts_as_json(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 977702,
                "title": "Default",
                "component": False,
                "content_type": "page",
                "updated_at": "2026-04-25T12:00:00Z",
                "body": "should-not-leak-into-list",  # body intentionally stripped from list view
            },
        ]
        result = layouts_resources.read_resource("voog://stella/layouts", client)
        client.get_all.assert_called_once_with("/layouts")
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item["id"], 977702)
        self.assertEqual(item["title"], "Default")
        self.assertFalse(item["component"])
        self.assertEqual(item["content_type"], "page")
        # body must NOT leak into the list projection
        self.assertNotIn("body", item)

    def test_read_root_empty(self):
        client = MagicMock()
        client.get_all.return_value = []
        result = layouts_resources.read_resource("voog://stella/layouts", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed, [])


class TestLayoutsResourcesReadSingleLayout(unittest.TestCase):
    def test_read_single_layout_returns_body_as_text_plain(self):
        client = MagicMock()
        body_src = "<!DOCTYPE html>\n<html>{% include 'header' %}</html>"
        client.get.return_value = {
            "id": 977702,
            "title": "Default",
            "body": body_src,
            "content_type": "page",
        }
        result = layouts_resources.read_resource("voog://stella/layouts/977702", client)
        client.get.assert_called_once_with("/layouts/977702")
        client.get_all.assert_not_called()
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "text/plain")
        # The raw .tpl source is the content (NOT JSON-wrapped)
        self.assertEqual(contents[0].content, body_src)

    def test_read_single_layout_missing_body_returns_empty_string(self):
        client = MagicMock()
        client.get.return_value = {"id": 977702, "title": "Empty"}  # body absent
        result = layouts_resources.read_resource("voog://stella/layouts/977702", client)
        contents = list(result)
        self.assertEqual(contents[0].mime_type, "text/plain")
        self.assertEqual(contents[0].content, "")

    def test_read_single_layout_null_body_returns_empty_string(self):
        client = MagicMock()
        client.get.return_value = {"id": 977702, "title": "Null", "body": None}
        result = layouts_resources.read_resource("voog://stella/layouts/977702", client)
        contents = list(result)
        self.assertEqual(contents[0].content, "")

    def test_read_single_layout_empty_string_body_passes_through(self):
        # API explicitly returning body="" should NOT be coerced to anything else —
        # `"" or ""` collapses to "" via the same fallback path. Pinning behaviour
        # so any future fallback rewrite (e.g. body=None checks) doesn't surprise
        # callers reading an explicitly empty .tpl.
        client = MagicMock()
        client.get.return_value = {"id": 977702, "title": "Empty Tpl", "body": ""}
        result = layouts_resources.read_resource("voog://stella/layouts/977702", client)
        contents = list(result)
        self.assertEqual(contents[0].mime_type, "text/plain")
        self.assertEqual(contents[0].content, "")

    def test_read_single_layout_rejects_non_integer_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/layouts/abc", client)
        client.get.assert_not_called()

    def test_read_single_layout_rejects_zero_or_negative(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/layouts/0", client)
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/layouts/-1", client)


class TestLayoutsResourcesUnknownUri(unittest.TestCase):
    def test_bare_trailing_slash_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/layouts/", client)

    def test_subpath_rejected(self):
        # voog://stella/layouts/{id}/anything is NOT a valid layouts URI
        client = MagicMock()
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/layouts/977702/contents", client)

    def test_completely_unrelated_uri_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            layouts_resources.read_resource("voog://stella/pages", client)


class TestLayoutsResourcesErrorPropagation(unittest.TestCase):
    def test_root_propagates_api_errors(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        with self.assertRaises(urllib.error.URLError):
            layouts_resources.read_resource("voog://stella/layouts", client)

    def test_single_layout_propagates_api_errors(self):
        client = MagicMock()
        client.get.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            layouts_resources.read_resource("voog://stella/layouts/999", client)


class TestServerResourceRegistry(unittest.TestCase):
    """Phase D contract — layouts resources joined to RESOURCE_GROUPS."""

    def test_layouts_in_resource_groups(self):
        from voog.mcp import server

        self.assertIn(layouts_resources, server.RESOURCE_GROUPS)

    def test_no_uri_collisions_after_layouts_added(self):
        from voog.mcp import server

        all_uris = [str(r.uri) for g in server.RESOURCE_GROUPS for r in g.get_resources()]
        self.assertEqual(len(all_uris), len(set(all_uris)), f"Duplicate resource URIs: {all_uris}")


if __name__ == "__main__":
    unittest.main()
