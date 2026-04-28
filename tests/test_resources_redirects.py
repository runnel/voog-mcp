"""Tests for voog_mcp.resources.redirects."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock


from voog.mcp.resources import redirects as redirects_resources


class TestRedirectsResources(unittest.TestCase):
    def test_get_resources_returns_one(self):
        resources = redirects_resources.get_resources()
        self.assertEqual(len(resources), 1)
        r = resources[0]
        self.assertEqual(str(r.uri), "voog://redirects")
        self.assertEqual(r.mimeType, "application/json")
        self.assertTrue(r.name)
        self.assertTrue(r.description)

    def test_matches_exact_redirects_uri(self):
        self.assertTrue(redirects_resources.matches("voog://redirects"))

    def test_matches_rejects_other_uris(self):
        self.assertFalse(redirects_resources.matches("voog://pages"))
        self.assertFalse(redirects_resources.matches("voog://redirects/123"))
        self.assertFalse(redirects_resources.matches("voog://redirectsx"))
        self.assertFalse(redirects_resources.matches(""))

    def test_read_resource_returns_redirects_list_as_json(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "source": "/old", "destination": "/new", "redirect_type": 301, "active": True},
            {"id": 2, "source": "/x", "destination": "/y", "redirect_type": 302, "active": True},
        ]
        result = redirects_resources.read_resource("voog://redirects", client)
        client.get_all.assert_called_once_with("/redirect_rules")
        # Returns iterable of ReadResourceContents
        contents = list(result)
        self.assertEqual(len(contents), 1)
        item = contents[0]
        self.assertEqual(item.mime_type, "application/json")
        parsed = json.loads(item.content)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["source"], "/old")

    def test_read_resource_empty_list_serializes_cleanly(self):
        client = MagicMock()
        client.get_all.return_value = []
        result = redirects_resources.read_resource("voog://redirects", client)
        contents = list(result)
        parsed = json.loads(contents[0].content)
        self.assertEqual(parsed, [])

    def test_read_resource_unknown_uri_raises(self):
        client = MagicMock()
        with self.assertRaises(ValueError):
            redirects_resources.read_resource("voog://other", client)
        client.get_all.assert_not_called()

    def test_read_resource_propagates_api_errors(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        # Resource read errors propagate — server layer wraps them as JSON-RPC errors
        with self.assertRaises(urllib.error.URLError):
            redirects_resources.read_resource("voog://redirects", client)


class TestServerResourceRegistry(unittest.TestCase):
    """Phase D dispatcher pattern — RESOURCE_GROUPS in server.py.

    These tests are CRITICAL for Tasks 15-18: each parallel session adds a
    new module to ``RESOURCE_GROUPS`` independently, and these tests catch
    contract violations BEFORE merge:

    - ``test_each_group_exports_required_callables`` — fails if a new
      group forgets ``get_resources`` / ``matches`` / ``read_resource``.
    - ``test_no_uri_collisions_across_groups`` — fails if two groups
      claim the same URI (e.g. both pages and articles claim
      ``voog://pages``). Without this, the first-match dispatcher in
      ``handle_read_resource`` would silently route to whichever group
      registered first.
    - ``test_resource_groups_includes_redirects`` — sentinel that
      registry itself wasn't accidentally cleared.

    Anyone adding a Phase D group should expect these tests to either
    pass automatically (if their module conforms) or guide them to the
    contract via the failure messages.
    """

    def test_resource_groups_includes_redirects(self):
        from voog.mcp import server
        self.assertIn(redirects_resources, server.RESOURCE_GROUPS)

    def test_no_uri_collisions_across_groups(self):
        from voog.mcp import server
        all_uris = [
            str(r.uri)
            for g in server.RESOURCE_GROUPS
            for r in g.get_resources()
        ]
        self.assertEqual(len(all_uris), len(set(all_uris)),
                         f"Duplicate resource URIs: {all_uris}")

    def test_each_group_exports_required_callables(self):
        from voog.mcp import server
        for g in server.RESOURCE_GROUPS:
            self.assertTrue(callable(getattr(g, "get_resources", None)),
                            f"{g.__name__} missing get_resources()")
            self.assertTrue(callable(getattr(g, "matches", None)),
                            f"{g.__name__} missing matches()")
            self.assertTrue(callable(getattr(g, "read_resource", None)),
                            f"{g.__name__} missing read_resource()")


if __name__ == "__main__":
    unittest.main()
