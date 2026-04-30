"""Tests for voog_mcp.tools.layouts."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from tests._test_helpers import _ann_get
from voog.mcp.tools import layouts as layouts_tools


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_three(self):
        tools = layouts_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(
            names,
            [
                "layout_rename",
                "layout_create",
                "asset_replace",
                "layout_update",
                "layout_delete",
                "layout_asset_create",
                "layout_asset_update",
                "layout_asset_delete",
            ],
        )

    def test_layout_rename_schema(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        schema = tools["layout_rename"].inputSchema
        self.assertEqual(schema["properties"]["layout_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["new_title"]["type"], "string")
        self.assertIn("layout_id", schema["required"])
        self.assertIn("new_title", schema["required"])

    def test_layout_create_schema(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        schema = tools["layout_create"].inputSchema
        self.assertEqual(schema["properties"]["title"]["type"], "string")
        self.assertEqual(schema["properties"]["body"]["type"], "string")
        self.assertEqual(schema["properties"]["kind"]["type"], "string")
        self.assertEqual(schema["properties"]["kind"]["enum"], ["layout", "component"])
        for req in ("title", "body", "kind"):
            self.assertIn(req, schema["required"])

    def test_asset_replace_schema(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        schema = tools["asset_replace"].inputSchema
        self.assertEqual(schema["properties"]["asset_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["new_filename"]["type"], "string")
        self.assertIn("asset_id", schema["required"])
        self.assertIn("new_filename", schema["required"])

    def test_all_tools_have_explicit_annotations(self):
        # Per PR #27 review: MCP spec defaults destructiveHint=true when
        # readOnlyHint=false. All three layouts tools must explicitly set
        # the trio (readOnlyHint, destructiveHint, idempotentHint).
        tools = layouts_tools.get_tools()
        for tool in tools:
            ann = tool.annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"),
                False,
                f"{tool.name} must have readOnlyHint=False explicitly",
            )

    def test_layout_rename_annotations(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        ann = tools["layout_rename"].annotations
        # Reversible (rename back to original title)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        # Idempotent (renaming to the same title twice = same end state)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_layout_create_annotations(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        ann = tools["layout_create"].annotations
        # Additive — creates new resource. Not destructive.
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        # NOT idempotent: calling twice creates two layouts with different ids
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)

    def test_asset_replace_annotations(self):
        tools = {t.name: t for t in layouts_tools.get_tools()}
        ann = tools["asset_replace"].annotations
        # Creates a NEW asset; old one is left in place (per voog.py docstring).
        # Not destructive (the old asset stays); not idempotent (each call → new id).
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)


class TestLayoutRename(unittest.TestCase):
    def test_success_calls_put(self):
        client = MagicMock()
        client.put.return_value = {"id": 977702, "title": "Default v2"}
        result = layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 977702, "new_title": "Default v2"},
            client,
        )
        client.put.assert_called_once_with("/layouts/977702", {"title": "Default v2"})
        self.assertEqual(len(result), 2)  # summary + JSON

    def test_rejects_title_with_forward_slash(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 1, "new_title": "foo/bar"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_rejects_title_with_backslash(self):
        client = MagicMock()
        layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 1, "new_title": "foo\\bar"},
            client,
        )
        client.put.assert_not_called()

    def test_rejects_title_starting_with_dot(self):
        client = MagicMock()
        layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 1, "new_title": ".hidden"},
            client,
        )
        client.put.assert_not_called()

    def test_rejects_empty_title(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 1, "new_title": ""},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_api_error_returns_error_response(self):
        client = MagicMock()
        client.put.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        result = layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": 999, "new_title": "Whatever"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("layout_rename", payload["error"])


class TestLayoutCreate(unittest.TestCase):
    def test_create_layout_posts_with_content_type(self):
        client = MagicMock()
        client.post.return_value = {"id": 999, "title": "Page A", "component": False}
        body = "<!DOCTYPE html><html>{{ content }}</html>"
        result = layouts_tools.call_tool(
            "layout_create",
            {"title": "Page A", "body": body, "kind": "layout"},
            client,
        )
        client.post.assert_called_once_with(
            "/layouts",
            {
                "title": "Page A",
                "body": body,
                "component": False,
                "content_type": "page",
            },
        )
        self.assertEqual(len(result), 2)

    def test_create_component_omits_content_type(self):
        # Voog API: components don't accept content_type field
        client = MagicMock()
        client.post.return_value = {"id": 1000, "title": "site-header", "component": True}
        body = "<header>...</header>"
        layouts_tools.call_tool(
            "layout_create",
            {"title": "site-header", "body": body, "kind": "component"},
            client,
        )
        args, _ = client.post.call_args
        payload = args[1]
        self.assertEqual(payload["component"], True)
        self.assertNotIn("content_type", payload)

    def test_create_layout_explicit_content_type_blog_article(self):
        # example.com already has 2 layouts with content_type=blog_article;
        # MCP must allow creating these (PR #28 review caught this gap).
        client = MagicMock()
        client.post.return_value = {"id": 999, "title": "Post", "component": False}
        layouts_tools.call_tool(
            "layout_create",
            {
                "title": "Post",
                "body": "{{ article.body }}",
                "kind": "layout",
                "content_type": "blog_article",
            },
            client,
        )
        args, _ = client.post.call_args
        payload = args[1]
        self.assertEqual(payload["content_type"], "blog_article")

    def test_create_layout_explicit_content_type_blog(self):
        client = MagicMock()
        client.post.return_value = {"id": 999, "title": "Index", "component": False}
        layouts_tools.call_tool(
            "layout_create",
            {
                "title": "Index",
                "body": "{% for a in articles %}{% endfor %}",
                "kind": "layout",
                "content_type": "blog",
            },
            client,
        )
        args, _ = client.post.call_args
        payload = args[1]
        self.assertEqual(payload["content_type"], "blog")

    def test_create_component_ignores_content_type_argument(self):
        # Even if a caller passes content_type with kind='component', it must
        # NOT end up in the payload — Voog rejects content_type on components.
        client = MagicMock()
        client.post.return_value = {"id": 1001, "title": "header", "component": True}
        layouts_tools.call_tool(
            "layout_create",
            {
                "title": "header",
                "body": "<header/>",
                "kind": "component",
                "content_type": "blog_article",  # nonsense for component
            },
            client,
        )
        args, _ = client.post.call_args
        payload = args[1]
        self.assertNotIn("content_type", payload)

    def test_create_layout_invalid_content_type_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_create",
            {
                "title": "X",
                "body": "y",
                "kind": "layout",
                "content_type": "wat",
            },
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("content_type", payload["error"])

    def test_create_layout_default_content_type_is_page(self):
        # When content_type is omitted entirely, default 'page' is sent
        client = MagicMock()
        client.post.return_value = {"id": 1, "title": "X", "component": False}
        layouts_tools.call_tool(
            "layout_create",
            {"title": "X", "body": "y", "kind": "layout"},
            client,
        )
        args, _ = client.post.call_args
        payload = args[1]
        self.assertEqual(payload["content_type"], "page")

    def test_invalid_kind_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_create",
            {"title": "X", "body": "y", "kind": "invalid"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_empty_title_rejected(self):
        client = MagicMock()
        layouts_tools.call_tool(
            "layout_create",
            {"title": "", "body": "y", "kind": "layout"},
            client,
        )
        client.post.assert_not_called()

    def test_title_with_slash_rejected(self):
        # Title-validation reused from layout_rename (same Voog rules)
        client = MagicMock()
        layouts_tools.call_tool(
            "layout_create",
            {"title": "foo/bar", "body": "y", "kind": "layout"},
            client,
        )
        client.post.assert_not_called()

    def test_post_response_missing_id_returns_error(self):
        # Defensive: Voog responding with no id is a contract violation
        client = MagicMock()
        client.post.return_value = {"title": "x"}  # no id
        result = layouts_tools.call_tool(
            "layout_create",
            {"title": "x", "body": "y", "kind": "layout"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_api_error_returns_error_response(self):
        client = MagicMock()
        client.post.side_effect = urllib.error.HTTPError(
            "url", 422, "Unprocessable Entity", {}, None
        )
        result = layouts_tools.call_tool(
            "layout_create",
            {"title": "x", "body": "y", "kind": "layout"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestAssetReplace(unittest.TestCase):
    def test_success_get_then_post(self):
        client = MagicMock()
        # Old asset has data + filename + asset_type
        client.get.return_value = {
            "id": 100,
            "filename": "old.css",
            "asset_type": "css",
            "data": "body { color: red; }",
        }
        client.post.return_value = {"id": 101, "filename": "new.css"}
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 100, "new_filename": "new.css"},
            client,
        )
        client.get.assert_called_once_with("/layout_assets/100")
        client.post.assert_called_once_with(
            "/layout_assets",
            {
                "filename": "new.css",
                "asset_type": "css",
                "data": "body { color: red; }",
            },
        )
        # Result should mention both old and new ids
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["old_id"], 100)
        self.assertEqual(breakdown["new_id"], 101)
        self.assertIn("warning", breakdown)  # warns about old asset still present

    def test_filename_with_slash_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 100, "new_filename": "foo/bar.css"},
            client,
        )
        client.get.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_filename_starting_with_dot_rejected(self):
        client = MagicMock()
        layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 100, "new_filename": ".hidden"},
            client,
        )
        client.get.assert_not_called()

    def test_get_missing_data_field_returns_error(self):
        # If old asset has no 'data' (some asset_types return data via separate URL),
        # tool can't replace without it — return error rather than POST empty data
        client = MagicMock()
        client.get.return_value = {
            "id": 100,
            "filename": "old.png",
            "asset_type": "image",
            # no 'data' field
        }
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 100, "new_filename": "new.png"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("data", payload["error"])

    def test_post_missing_id_returns_error(self):
        client = MagicMock()
        client.get.return_value = {"id": 100, "filename": "x", "asset_type": "css", "data": "x"}
        client.post.return_value = {"filename": "y"}  # no id
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 100, "new_filename": "y.css"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_get_api_error_returns_error_response(self):
        client = MagicMock()
        client.get.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": 999, "new_filename": "x.css"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestLayoutUpdate(unittest.TestCase):
    def test_update_body(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5}
        layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "body": "<h1>{{ page.title }}</h1>"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/layouts/5")
        self.assertEqual(body["body"], "<h1>{{ page.title }}</h1>")
        self.assertNotIn("title", body)

    def test_update_title_and_body(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5}
        layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "title": "Renamed", "body": "x"},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["title"], "Renamed")
        self.assertEqual(body["body"], "x")

    def test_rejects_unsafe_title(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "title": "../escape"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_rejects_empty_call(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_update", {"layout_id": 5}, client
        )
        self.assertTrue(result.isError)


class TestLayoutDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_delete", {"layout_id": 5}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 5, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/layouts/5")


class TestLayoutAssetCreate(unittest.TestCase):
    def test_create_text_asset(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.post.return_value = {"id": 99, "filename": "main.css"}
        layouts_tools.call_tool(
            "layout_asset_create",
            {
                "filename": "main.css",
                "asset_type": "stylesheet",
                "data": "body{margin:0}",
            },
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/layout_assets")
        self.assertEqual(body["filename"], "main.css")
        self.assertEqual(body["data"], "body{margin:0}")

    def test_rejects_unsafe_filename(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_create",
            {
                "filename": "../etc/passwd",
                "asset_type": "stylesheet",
                "data": "x",
            },
            client,
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()


class TestLayoutAssetUpdate(unittest.TestCase):
    def test_put_data(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 99}
        layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "body{margin:0;padding:0}"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/layout_assets/99")
        self.assertEqual(body, {"data": "body{margin:0;padding:0}"})

    def test_rejects_filename_change(self):
        # Skill memory: PUT /layout_assets/{id} with filename returns 500.
        # Refuse client-side and point at asset_replace.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "x", "filename": "new.css"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("asset_replace", payload["error"])


class TestLayoutAssetDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_delete", {"asset_id": 99}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_deletes(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        layouts_tools.call_tool(
            "layout_asset_delete",
            {"asset_id": 99, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/layout_assets/99")


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = MagicMock()
        result = layouts_tools.call_tool("nonexistent", {}, client)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — layouts joined to TOOL_GROUPS."""

    def test_layouts_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(layouts_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        from voog.mcp import server

        all_names = [tool.name for group in server.TOOL_GROUPS for tool in group.get_tools()]
        self.assertEqual(len(all_names), len(set(all_names)), f"Duplicate tool names: {all_names}")


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import layouts as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


if __name__ == "__main__":
    unittest.main()
