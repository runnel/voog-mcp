"""Schema-default vs handler-fallback drift tests.

PR #108 review nit: JSON Schema ``"default"`` values are descriptive
(MCP doesn't auto-fill from them). The actual defaults come from the
handler's ``arguments.get(key, fallback)`` calls. If a contributor
changes the schema default but forgets the handler fallback (or vice
versa), the doc-vs-behaviour drift goes undetected. These tests
explicitly bind schema and handler together for the tools where
defaults matter most:

  - ``redirect_add`` — `redirect_type` (301), `active` (True), `regexp` (False)
  - Destructive `force=False` gates on `redirect_delete`, `layout_delete`,
    `page_delete`, `article_delete`, `page_delete_data`,
    ``article_delete_data``.

If a contributor moves either the schema default or the handler fallback,
this test catches the drift.
"""

import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import redirects as redirects_tools


def _schema_default(tool, prop_name):
    """Return the ``default`` value from a tool's schema property, or None."""
    return tool.inputSchema["properties"].get(prop_name, {}).get("default")


class TestRedirectAddSchemaDefaults(unittest.TestCase):
    """redirect_add has 3 documented defaults: redirect_type, active, regexp."""

    def setUp(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        self.tool = tools["redirect_add"]

    def test_redirect_type_default_matches_handler(self):
        # Schema says default=301; handler should send 301 when omitted.
        self.assertEqual(_schema_default(self.tool, "redirect_type"), 301)
        client = MagicMock()
        client.post.return_value = {"id": 1}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old", "destination": "/new"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertEqual(sent_body["redirect_rule"]["redirect_type"], 301)

    def test_active_default_matches_handler(self):
        self.assertIs(_schema_default(self.tool, "active"), True)
        client = MagicMock()
        client.post.return_value = {"id": 1}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old", "destination": "/new"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["active"], True)

    def test_regexp_default_matches_handler(self):
        self.assertIs(_schema_default(self.tool, "regexp"), False)
        client = MagicMock()
        client.post.return_value = {"id": 1}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old", "destination": "/new"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["regexp"], False)


class TestForceFlagSchemaDefaults(unittest.TestCase):
    """Destructive tools share a ``force=False`` schema default that gates
    the destructive op. If the schema and handler drift, a destructive
    call could fire without explicit ``force=true``.
    """

    def test_redirect_delete_force_default_false(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        self.assertIs(_schema_default(tools["redirect_delete"], "force"), False)
        # Verify the handler refuses without force=true (drift would let
        # destructive ops fire silently).
        client = MagicMock()
        result = redirects_tools.call_tool("redirect_delete", {"redirect_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_layout_delete_force_default_false(self):
        from voog.mcp.tools import layouts as layouts_tools

        tools = {t.name: t for t in layouts_tools.get_tools()}
        self.assertIs(_schema_default(tools["layout_delete"], "force"), False)
        client = MagicMock()
        result = layouts_tools.call_tool("layout_delete", {"layout_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_page_delete_force_default_false(self):
        from voog.mcp.tools import pages_mutate as pages_mutate_tools

        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        self.assertIs(_schema_default(tools["page_delete"], "force"), False)
        client = MagicMock()
        result = pages_mutate_tools.call_tool("page_delete", {"page_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_article_delete_force_default_false(self):
        from voog.mcp.tools import articles as articles_tools

        tools = {t.name: t for t in articles_tools.get_tools()}
        self.assertIs(_schema_default(tools["article_delete"], "force"), False)
        client = MagicMock()
        result = articles_tools.call_tool("article_delete", {"article_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_page_delete_data_force_default_false(self):
        from voog.mcp.tools import pages_mutate as pages_mutate_tools

        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        self.assertIs(_schema_default(tools["page_delete_data"], "force"), False)
        client = MagicMock()
        result = pages_mutate_tools.call_tool(
            "page_delete_data", {"page_id": 1, "key": "x"}, client
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_article_delete_data_force_default_false(self):
        from voog.mcp.tools import articles as articles_tools

        tools = {t.name: t for t in articles_tools.get_tools()}
        self.assertIs(_schema_default(tools["article_delete_data"], "force"), False)
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_delete_data", {"article_id": 1, "key": "x"}, client
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_layout_asset_delete_force_default_false(self):
        from voog.mcp.tools import layouts as layouts_tools

        tools = {t.name: t for t in layouts_tools.get_tools()}
        self.assertIs(_schema_default(tools["layout_asset_delete"], "force"), False)
        client = MagicMock()
        result = layouts_tools.call_tool("layout_asset_delete", {"asset_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_site_delete_data_force_default_false(self):
        from voog.mcp.tools import site as site_tools

        tools = {t.name: t for t in site_tools.get_tools()}
        self.assertIs(_schema_default(tools["site_delete_data"], "force"), False)
        client = MagicMock()
        result = site_tools.call_tool("site_delete_data", {"key": "x"}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_language_delete_force_default_false(self):
        from voog.mcp.tools import multilingual as multilingual_tools

        tools = {t.name: t for t in multilingual_tools.get_tools()}
        self.assertIs(_schema_default(tools["language_delete"], "force"), False)
        client = MagicMock()
        result = multilingual_tools.call_tool("language_delete", {"language_id": 1}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)


if __name__ == "__main__":
    unittest.main()
