"""Resource URI collision detection — startup contract.

server.py wires the live RESOURCE_GROUPS into a first-match dispatcher in
``handle_read_resource``. Two groups silently claiming overlapping URIs
would route to whichever registered first — surprising the caller. These
tests cover the explicit collision guard that runs at server startup
(mirroring the existing tool name collision check on line 60-67 of
server.py).

Each resource module exports ``get_uri_patterns() -> list[str]`` returning
the URI / URI_PREFIX strings it claims. The guard fails-fast if two
groups claim the same string, or if one claim is a strict sub-path of
another (e.g. ``voog://pages`` and ``voog://pages/special``: ``matches()``
on both groups would resolve true for ``voog://pages/special/foo``).
"""

import unittest
from types import SimpleNamespace

from voog.mcp import server
from voog.mcp.resources import (
    articles as articles_resources,
)
from voog.mcp.resources import (
    layouts as layouts_resources,
)
from voog.mcp.resources import (
    pages as pages_resources,
)
from voog.mcp.resources import (
    products as products_resources,
)
from voog.mcp.resources import (
    redirects as redirects_resources,
)


def _fake_group(name: str, patterns: list[str]):
    """Build a minimal stand-in resource group for collision tests."""
    return SimpleNamespace(
        __name__=name,
        get_uri_patterns=lambda patterns=patterns: list(patterns),
    )


class TestResourceUriPatternsContract(unittest.TestCase):
    """Each live resource group must expose ``get_uri_patterns()``."""

    def test_redirects_exposes_patterns(self):
        self.assertEqual(redirects_resources.get_uri_patterns(), ["voog://{site}/redirects"])

    def test_pages_exposes_patterns(self):
        self.assertEqual(pages_resources.get_uri_patterns(), ["voog://{site}/pages"])

    def test_layouts_exposes_patterns(self):
        self.assertEqual(layouts_resources.get_uri_patterns(), ["voog://{site}/layouts"])

    def test_articles_exposes_patterns(self):
        self.assertEqual(articles_resources.get_uri_patterns(), ["voog://{site}/articles"])

    def test_products_exposes_patterns(self):
        self.assertEqual(products_resources.get_uri_patterns(), ["voog://{site}/products"])

    def test_each_live_group_exports_get_uri_patterns(self):
        for g in server.RESOURCE_GROUPS:
            patterns = getattr(g, "get_uri_patterns", None)
            self.assertTrue(
                callable(patterns),
                f"{g.__name__} missing get_uri_patterns()",
            )
            self.assertIsInstance(patterns(), list)
            self.assertTrue(
                all(isinstance(p, str) and p for p in patterns()),
                f"{g.__name__}.get_uri_patterns() must return non-empty strings",
            )


class TestValidateResourceUriPatterns(unittest.TestCase):
    """Startup-time collision/overlap detection."""

    def test_live_resource_groups_have_no_collisions(self):
        # Sanity: the 5 production groups must pass the guard.
        server._validate_resource_uri_patterns(server.RESOURCE_GROUPS)

    def test_duplicate_pattern_across_groups_raises(self):
        groups = [
            _fake_group("voog.mcp.resources.pages", ["voog://pages"]),
            _fake_group("voog.mcp.resources.fake_pages", ["voog://pages"]),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            server._validate_resource_uri_patterns(groups)
        msg = str(ctx.exception)
        self.assertIn("voog://pages", msg)
        self.assertIn("voog.mcp.resources.pages", msg)
        self.assertIn("voog.mcp.resources.fake_pages", msg)

    def test_prefix_overlap_strict_subpath_raises(self):
        # "voog://pages/special" startswith "voog://pages/" -> both match
        # "voog://pages/special/foo".
        groups = [
            _fake_group("voog.mcp.resources.pages", ["voog://pages"]),
            _fake_group("voog.mcp.resources.pages_special", ["voog://pages/special"]),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            server._validate_resource_uri_patterns(groups)
        msg = str(ctx.exception)
        self.assertIn("voog://pages", msg)
        self.assertIn("voog://pages/special", msg)

    def test_prefix_overlap_reverse_order_raises(self):
        # Same as above but registered in reverse order — the guard must
        # be order-independent.
        groups = [
            _fake_group("voog.mcp.resources.pages_special", ["voog://pages/special"]),
            _fake_group("voog.mcp.resources.pages", ["voog://pages"]),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            server._validate_resource_uri_patterns(groups)
        self.assertIn("voog://pages", str(ctx.exception))

    def test_pagesx_vs_pages_does_not_collide(self):
        # "voog://pagesx" does NOT startswith "voog://pages/", so the live
        # ``matches()`` would correctly reject it. Guard must agree — no
        # false positive here.
        groups = [
            _fake_group("voog.mcp.resources.pages", ["voog://pages"]),
            _fake_group("voog.mcp.resources.pagesx", ["voog://pagesx"]),
        ]
        server._validate_resource_uri_patterns(groups)

    def test_distinct_top_level_prefixes_pass(self):
        groups = [
            _fake_group("voog.mcp.resources.pages", ["voog://pages"]),
            _fake_group("voog.mcp.resources.layouts", ["voog://layouts"]),
            _fake_group("voog.mcp.resources.articles", ["voog://articles"]),
        ]
        server._validate_resource_uri_patterns(groups)


if __name__ == "__main__":
    unittest.main()
