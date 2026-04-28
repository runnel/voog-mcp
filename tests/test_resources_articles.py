"""Tests for voog_mcp.resources.articles."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock


from voog.mcp.resources import articles as articles_resources


class TestArticlesResourcesGetResources(unittest.TestCase):
    def test_get_resources_returns_listable_root(self):
        resources = articles_resources.get_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(str(resources[0].uri), "voog://articles")
        self.assertEqual(resources[0].mimeType, "application/json")
        self.assertTrue(resources[0].name)
        self.assertTrue(resources[0].description)


class TestArticlesResourcesMatches(unittest.TestCase):
    def test_matches_root_uri(self):
        self.assertTrue(articles_resources.matches("voog://articles"))

    def test_matches_single_article_uri(self):
        self.assertTrue(articles_resources.matches("voog://articles/12345"))

    def test_does_not_match_other_groups(self):
        self.assertFalse(articles_resources.matches("voog://pages"))
        self.assertFalse(articles_resources.matches("voog://layouts"))
        self.assertFalse(articles_resources.matches("voog://redirects"))

    def test_does_not_match_prefix_lookalike(self):
        self.assertFalse(articles_resources.matches("voog://articlesx"))
        self.assertFalse(articles_resources.matches("voog://articles-old"))

    def test_does_not_match_empty(self):
        self.assertFalse(articles_resources.matches(""))
        self.assertFalse(articles_resources.matches("voog://"))


class TestArticlesResourcesReadRoot(unittest.TestCase):
    def test_read_root_returns_simplified_articles_as_json(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 5001,
                "title": "Hello World",
                "path": "blog/hello-world",
                "public_url": "https://runnel.ee/blog/hello-world",
                "published": True,
                "published_at": "2026-04-01T00:00:00Z",
                "updated_at": "2026-04-15T00:00:00Z",
                "created_at": "2026-04-01T00:00:00Z",
                "language": {"code": "et"},
                "page": {"id": 999},
                "body": "should-not-leak-into-list",  # body intentionally stripped
            },
        ]
        result = articles_resources.read_resource("voog://articles", client)
        client.get_all.assert_called_once_with("/articles")
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "application/json")
        parsed = json.loads(contents[0].content)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item["id"], 5001)
        self.assertEqual(item["title"], "Hello World")
        self.assertEqual(item["path"], "blog/hello-world")
        self.assertTrue(item["published"])
        self.assertEqual(item["language_code"], "et")
        self.assertEqual(item["page_id"], 999)
        # body must NOT leak into the list projection
        self.assertNotIn("body", item)

    def test_read_root_handles_missing_nested_objects(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Bare"},  # no language, no page
        ]
        result = articles_resources.read_resource("voog://articles", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed[0]["id"], 1)
        self.assertIsNone(parsed[0]["language_code"])
        self.assertIsNone(parsed[0]["page_id"])

    def test_read_root_empty(self):
        client = MagicMock()
        client.get_all.return_value = []
        result = articles_resources.read_resource("voog://articles", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed, [])


class TestArticlesResourcesReadSingleArticle(unittest.TestCase):
    def test_read_single_article_returns_body_as_text_html(self):
        client = MagicMock()
        body_html = "<h1>Hello</h1><p>World</p>"
        client.get.return_value = {
            "id": 5001,
            "title": "Hello World",
            "body": body_html,
        }
        result = articles_resources.read_resource("voog://articles/5001", client)
        client.get.assert_called_once_with("/articles/5001")
        client.get_all.assert_not_called()
        contents = list(result)
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].mime_type, "text/html")
        self.assertEqual(contents[0].content, body_html)

    def test_read_single_article_missing_body_returns_empty_string(self):
        client = MagicMock()
        client.get.return_value = {"id": 5001, "title": "Empty"}  # no body
        result = articles_resources.read_resource("voog://articles/5001", client)
        contents = list(result)
        self.assertEqual(contents[0].mime_type, "text/html")
        self.assertEqual(contents[0].content, "")

    def test_read_single_article_null_body_returns_empty_string(self):
        client = MagicMock()
        client.get.return_value = {"id": 5001, "body": None}
        result = articles_resources.read_resource("voog://articles/5001", client)
        contents = list(result)
        self.assertEqual(contents[0].content, "")

    def test_read_single_article_empty_string_body_passes_through(self):
        # Pin behaviour: API returning body="" stays ""
        client = MagicMock()
        client.get.return_value = {"id": 5001, "body": ""}
        result = articles_resources.read_resource("voog://articles/5001", client)
        contents = list(result)
        self.assertEqual(contents[0].mime_type, "text/html")
        self.assertEqual(contents[0].content, "")

    def test_read_single_article_rejects_non_integer_id(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            articles_resources.read_resource("voog://articles/abc", client)
        client.get.assert_not_called()

    def test_read_single_article_rejects_zero_or_negative(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            articles_resources.read_resource("voog://articles/0", client)
        with self.assertRaises(ValueError):
            articles_resources.read_resource("voog://articles/-1", client)


class TestArticlesResourcesUnknownUri(unittest.TestCase):
    def test_bare_trailing_slash_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            articles_resources.read_resource("voog://articles/", client)

    def test_subpath_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            articles_resources.read_resource(
                "voog://articles/5001/comments", client
            )

    def test_completely_unrelated_uri_rejected(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            articles_resources.read_resource("voog://pages", client)


class TestArticlesResourcesErrorPropagation(unittest.TestCase):
    def test_root_propagates_api_errors(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        with self.assertRaises(urllib.error.URLError):
            articles_resources.read_resource("voog://articles", client)

    def test_single_article_propagates_api_errors(self):
        client = MagicMock()
        client.get.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        with self.assertRaises(urllib.error.HTTPError):
            articles_resources.read_resource("voog://articles/999", client)


class TestServerResourceRegistry(unittest.TestCase):
    """Phase D contract — articles resources joined to RESOURCE_GROUPS."""

    def test_articles_in_resource_groups(self):
        from voog.mcp import server
        self.assertIn(articles_resources, server.RESOURCE_GROUPS)

    def test_no_uri_collisions_after_articles_added(self):
        from voog.mcp import server
        all_uris = [
            str(r.uri)
            for g in server.RESOURCE_GROUPS
            for r in g.get_resources()
        ]
        self.assertEqual(len(all_uris), len(set(all_uris)),
                         f"Duplicate resource URIs: {all_uris}")


if __name__ == "__main__":
    unittest.main()
