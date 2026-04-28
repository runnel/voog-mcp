"""Tests for voog_mcp.resources.pages."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.resources import pages as pages_resources


class TestPagesResourcesGetResources(unittest.TestCase):
    def test_get_resources_returns_listable_root(self):
        resources = pages_resources.get_resources()
        # Only the listable list URI is exposed as a concrete Resource.
        # Per-page and per-page-contents are template URIs (read-only via read_resource).
        self.assertEqual(len(resources), 1)
        self.assertEqual(str(resources[0].uri), "voog://pages")
        self.assertEqual(resources[0].mimeType, "application/json")
        self.assertTrue(resources[0].name)
        self.assertTrue(resources[0].description)


class TestPagesResourcesMatches(unittest.TestCase):
    def test_matches_root_uri(self):
        self.assertTrue(pages_resources.matches("voog://pages"))

    def test_matches_single_page_uri(self):
        self.assertTrue(pages_resources.matches("voog://pages/152377"))

    def test_matches_page_contents_uri(self):
        self.assertTrue(pages_resources.matches("voog://pages/152377/contents"))

    def test_does_not_match_other_groups(self):
        self.assertFalse(pages_resources.matches("voog://layouts"))
        self.assertFalse(pages_resources.matches("voog://articles/1"))
        self.assertFalse(pages_resources.matches("voog://redirects"))

    def test_does_not_match_prefix_lookalike(self):
        # voog://pagesx must NOT be considered a pages URI — guards against
        # naive str.startswith("voog://pages") that would match "voog://pagesx"
        self.assertFalse(pages_resources.matches("voog://pagesx"))
        self.assertFalse(pages_resources.matches("voog://pages-archive"))

    def test_does_not_match_empty(self):
        self.assertFalse(pages_resources.matches(""))
        self.assertFalse(pages_resources.matches("voog://"))


class TestPagesResourcesReadRoot(unittest.TestCase):
    def test_read_root_returns_simplified_pages_as_json(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "path": "foo",
                "title": "Foo",
                "hidden": False,
                "layout_id": 10,
                "layout": {"id": 10, "title": "Default"},
                "content_type": "page",
                "parent_id": None,
                "language": {"code": "et"},
                "public_url": "https://runnel.ee/foo",
            },
        ]
        result = pages_resources.read_resource("voog://pages", client)
        client.get_all.assert_called_once_with("/pages")
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[0]["layout_name"], "Default")
        self.assertEqual(parsed[0]["language_code"], "et")

    def test_read_root_empty(self):
        client = MagicMock()
        client.get_all.return_value = []
        result = pages_resources.read_resource("voog://pages", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed, [])


class TestPagesResourcesReadSinglePage(unittest.TestCase):
    def test_read_single_page_calls_correct_endpoint(self):
        client = MagicMock()
        client.get.return_value = {"id": 152377, "title": "Avaleht", "path": ""}
        result = pages_resources.read_resource("voog://pages/152377", client)
        client.get.assert_called_once_with("/pages/152377")
        client.get_all.assert_not_called()
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed["id"], 152377)
        self.assertEqual(parsed["title"], "Avaleht")

    def test_read_single_page_rejects_non_integer_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource("voog://pages/abc", client)
        client.get.assert_not_called()

    def test_read_single_page_rejects_negative_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource("voog://pages/-5", client)


class TestPagesResourcesReadContents(unittest.TestCase):
    def test_read_contents_calls_correct_endpoint(self):
        client = MagicMock()
        client.get.return_value = [
            {"id": 1, "name": "title", "value": "Hello"},
            {"id": 2, "name": "body", "value": "World"},
        ]
        result = pages_resources.read_resource(
            "voog://pages/152377/contents", client
        )
        client.get.assert_called_once_with("/pages/152377/contents")
        contents = list(result)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(len(parsed), 2)

    def test_read_contents_rejects_non_integer_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource(
                "voog://pages/abc/contents", client
            )


class TestPagesResourcesUnknownUri(unittest.TestCase):
    def test_bare_trailing_slash_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource("voog://pages/", client)

    def test_unknown_subpath_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource(
                "voog://pages/152377/unknown", client
            )

    def test_unknown_extra_segments_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource(
                "voog://pages/152377/contents/extra", client
            )

    def test_completely_unrelated_uri_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            pages_resources.read_resource("voog://layouts", client)


class TestPagesResourcesErrorPropagation(unittest.TestCase):
    def test_root_propagates_api_errors(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        with self.assertRaises(urllib.error.URLError):
            pages_resources.read_resource("voog://pages", client)

    def test_single_page_propagates_api_errors(self):
        client = MagicMock()
        client.get.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        with self.assertRaises(urllib.error.HTTPError):
            pages_resources.read_resource("voog://pages/999", client)


class TestServerResourceRegistry(unittest.TestCase):
    """Phase D contract — pages resources joined to RESOURCE_GROUPS."""

    def test_pages_in_resource_groups(self):
        from voog_mcp import server
        self.assertIn(pages_resources, server.RESOURCE_GROUPS)

    def test_no_uri_collisions_after_pages_added(self):
        from voog_mcp import server
        all_uris = [
            str(r.uri)
            for g in server.RESOURCE_GROUPS
            for r in g.get_resources()
        ]
        self.assertEqual(len(all_uris), len(set(all_uris)),
                         f"Duplicate resource URIs: {all_uris}")


if __name__ == "__main__":
    unittest.main()
