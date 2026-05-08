"""Tests for voog.mcp.tools.redirects."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from tests._test_helpers import _ann_get
from voog.mcp.tools import redirects as redirects_tools


class TestRedirectsTools(unittest.TestCase):
    def test_get_tools_returns_four(self):
        tools = redirects_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(
            names,
            ["redirects_list", "redirect_add", "redirect_update", "redirect_delete"],
        )

    def test_redirects_list_full_annotation_triple(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        ann = tools["redirects_list"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_redirect_add_full_annotation_triple(self):
        # redirect_add is additive (creates a new rule), so destructiveHint=False.
        # NOT idempotent: repeat calls either create duplicate rules or error
        # on conflict — repeated calls have additional effect.
        tools = {t.name: t for t in redirects_tools.get_tools()}
        ann = tools["redirect_add"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)

    def test_redirect_add_schema_requires_source_and_destination(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        schema = tools["redirect_add"].inputSchema
        self.assertIn("source", schema["properties"])
        self.assertIn("destination", schema["properties"])
        self.assertIn("redirect_type", schema["properties"])
        # source + destination required, redirect_type optional (default 301)
        self.assertIn("source", schema["required"])
        self.assertIn("destination", schema["required"])
        self.assertNotIn("redirect_type", schema["required"])

    def test_redirects_list_calls_client(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "source": "/old", "destination": "/new", "redirect_type": 301},
        ]
        result = redirects_tools.call_tool("redirects_list", {}, client)
        client.get_all.assert_called_once_with("/redirect_rules")
        # success_response with summary → 2 TextContents
        self.assertEqual(len(result), 2)

    def test_redirect_add_calls_client_with_defaults(self):
        client = MagicMock()
        client.post.return_value = {
            "id": 99,
            "source": "/a",
            "destination": "/b",
            "redirect_type": 301,
        }
        result = redirects_tools.call_tool(
            "redirect_add",
            {"source": "/a", "destination": "/b"},
            client,
        )
        client.post.assert_called_once_with(
            "/redirect_rules",
            {
                "redirect_rule": {
                    "source": "/a",
                    "destination": "/b",
                    "redirect_type": 301,
                    "active": True,
                    "regexp": False,
                }
            },
        )
        self.assertEqual(len(result), 2)

    def test_redirect_add_passes_explicit_type(self):
        client = MagicMock()
        client.post.return_value = {"id": 100}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/x", "destination": "/y", "redirect_type": 410},
            client,
        )
        client.post.assert_called_once_with(
            "/redirect_rules",
            {
                "redirect_rule": {
                    "source": "/x",
                    "destination": "/y",
                    "redirect_type": 410,
                    "active": True,
                    "regexp": False,
                }
            },
        )

    def test_redirects_list_error_returns_error_response(self):
        client = MagicMock()
        client.get_all.side_effect = urllib.error.URLError("network down")
        result = redirects_tools.call_tool("redirects_list", {}, client)
        self.assertEqual(len(result.content), 1)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("redirects_list", payload["error"])

    def test_redirect_add_error_returns_error_response(self):
        client = MagicMock()
        client.post.side_effect = Exception("boom")
        result = redirects_tools.call_tool(
            "redirect_add",
            {"source": "/a", "destination": "/b"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("redirect_add", payload["error"])

    def test_call_tool_unknown_name_returns_error(self):
        client = MagicMock()
        result = redirects_tools.call_tool("nonexistent", {}, client)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_redirect_add_schema_exposes_regexp_and_active(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        props = tools["redirect_add"].inputSchema["properties"]
        self.assertIn("regexp", props)
        self.assertEqual(props["regexp"]["type"], "boolean")
        self.assertIn("active", props)
        self.assertEqual(props["active"]["type"], "boolean")

    def test_redirect_add_schema_regexp_and_active_optional(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        required = tools["redirect_add"].inputSchema["required"]
        self.assertNotIn("regexp", required)
        self.assertNotIn("active", required)

    def test_redirect_add_passes_regexp_to_client(self):
        client = MagicMock()
        client.post.return_value = {
            "id": 1,
            "source": "/old/.*",
            "destination": "/new",
            "redirect_type": 301,
            "regexp": True,
            "active": True,
        }
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old/.*", "destination": "/new", "regexp": True},
            client,
        )
        sent_path = client.post.call_args[0][0]
        sent_body = client.post.call_args[0][1]
        self.assertEqual(sent_path, "/redirect_rules")
        self.assertIs(sent_body["redirect_rule"]["regexp"], True)

    def test_redirect_add_defaults_regexp_false(self):
        client = MagicMock()
        client.post.return_value = {"id": 1, "source": "/old", "destination": "/new"}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old", "destination": "/new"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["regexp"], False)

    def test_redirect_add_passes_active_false(self):
        client = MagicMock()
        client.post.return_value = {"id": 1, "source": "/old", "destination": "/new"}
        redirects_tools.call_tool(
            "redirect_add",
            {"source": "/old", "destination": "/new", "active": False},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["active"], False)


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import redirects as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


class TestRedirectUpdate(unittest.TestCase):
    def test_update_destination(self):
        from voog.mcp.tools import redirects as redirects_tools

        # Voog's PUT /redirect_rules/{id} is full-replace (Rails-style):
        # missing fields get coerced to defaults. The tool now does
        # GET-then-merge-then-PUT so unspecified fields are preserved.
        client = MagicMock()
        client.get.return_value = {
            "id": 9,
            "source": "/old",
            "destination": "/old-dest",
            "redirect_type": 301,
            "active": True,
        }
        client.put.return_value = {"id": 9}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "destination": "/uus"},
            client,
        )
        client.get.assert_called_once_with("/redirect_rules/9")
        path, body = client.put.call_args.args
        self.assertEqual(path, "/redirect_rules/9")
        self.assertEqual(body["redirect_rule"]["destination"], "/uus")
        # Other fields preserved from GET.
        self.assertEqual(body["redirect_rule"]["source"], "/old")
        self.assertEqual(body["redirect_rule"]["redirect_type"], 301)
        self.assertIs(body["redirect_rule"]["active"], True)

    def test_update_redirect_type_validated(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        result = redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "redirect_type": 999},
            client,
        )
        self.assertTrue(result.isError)
        client.get.assert_not_called()
        client.put.assert_not_called()

    def test_update_active_flag(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        client.get.return_value = {
            "id": 9,
            "source": "/a",
            "destination": "/b",
            "redirect_type": 301,
            "active": True,
        }
        client.put.return_value = {"id": 9}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "active": False},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertIs(body["redirect_rule"]["active"], False)
        # Other fields preserved.
        self.assertEqual(body["redirect_rule"]["source"], "/a")
        self.assertEqual(body["redirect_rule"]["destination"], "/b")
        self.assertEqual(body["redirect_rule"]["redirect_type"], 301)

    def test_preserves_active_when_only_destination_changes(self):
        # Regression for the silent active=False → True coercion bug.
        # Voog's PUT is full-replace; previously the tool sent only the
        # provided fields, so missing `active` flipped to its default (True).
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        client.get.return_value = {
            "id": 9,
            "source": "/old",
            "destination": "/old-dest",
            "redirect_type": 302,
            "active": False,
        }
        client.put.return_value = {"id": 9}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "destination": "/new-dest"},
            client,
        )
        body = client.put.call_args.args[1]
        rr = body["redirect_rule"]
        self.assertIs(rr["active"], False)
        self.assertEqual(rr["source"], "/old")
        self.assertEqual(rr["destination"], "/new-dest")
        self.assertEqual(rr["redirect_type"], 302)

    def test_redirect_update_schema_exposes_regexp(self):
        tools = {t.name: t for t in redirects_tools.get_tools()}
        props = tools["redirect_update"].inputSchema["properties"]
        self.assertIn("regexp", props)
        self.assertEqual(props["regexp"]["type"], "boolean")

    def test_redirect_update_merges_regexp_into_put(self):
        client = MagicMock()
        # GET returns the existing rule; redirect_update merges and PUTs.
        client.get.return_value = {
            "id": 7,
            "source": "/old",
            "destination": "/new",
            "redirect_type": 301,
            "active": True,
            "regexp": False,
        }
        client.put.return_value = {"id": 7}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 7, "regexp": True},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["regexp"], True)
        # Other fields preserved from GET (B2 GET-merge-PUT contract).
        self.assertEqual(sent_body["redirect_rule"]["source"], "/old")
        self.assertEqual(sent_body["redirect_rule"]["destination"], "/new")
        self.assertIs(sent_body["redirect_rule"]["active"], True)

    def test_redirect_update_preserves_regexp_when_not_supplied(self):
        # If caller doesn't pass regexp, the GET-merge-PUT must echo back
        # whatever Voog had. Pre-fix REDIRECT_FIELDS lacked regexp, so the
        # PUT envelope omitted it and Voog coerced to default false.
        client = MagicMock()
        client.get.return_value = {
            "id": 7,
            "source": "/old",
            "destination": "/new",
            "redirect_type": 301,
            "active": True,
            "regexp": True,
        }
        client.put.return_value = {"id": 7}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 7, "destination": "/even-newer"},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertIs(sent_body["redirect_rule"]["regexp"], True)


class TestRedirectDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        result = redirects_tools.call_tool("redirect_delete", {"redirect_id": 9}, client)
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_deletes(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        redirects_tools.call_tool(
            "redirect_delete",
            {"redirect_id": 9, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/redirect_rules/9")


if __name__ == "__main__":
    unittest.main()
