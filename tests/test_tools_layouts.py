"""Tests for voog.mcp.tools.layouts."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from tests._test_helpers import _ann_get
from voog.mcp.tools import layouts as layouts_tools


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_every_layout_tool(self):
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
                "layout_asset_upload",
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


class TestLayoutsBoolReject(unittest.TestCase):
    """T4b: require_int guards — bools must not reach Voog as ids."""

    def test_layout_rename_layout_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_rename",
            {"layout_id": True, "new_title": "OK"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_asset_replace_asset_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "asset_replace",
            {"asset_id": False, "new_filename": "ok.css"},
            client,
        )
        self.assertTrue(result.isError)
        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_layout_update_layout_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": True, "body": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_layout_delete_layout_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": False, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_layout_asset_update_asset_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": True, "data": "body{}"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_layout_asset_delete_asset_id_bool_rejected(self):
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_delete",
            {"asset_id": False, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()


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
        result = layouts_tools.call_tool("layout_update", {"layout_id": 5}, client)
        self.assertTrue(result.isError)

    def test_silent_no_op_body_echo_back_empty(self):
        # Defense-in-depth (#99): if Voog ever regresses to echoing the
        # resource with `body` cleared (the original #96 symptom),
        # surface it as an error instead of silently lying.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5, "body": ""}
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "body": "<h1>x</h1>"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("silent no-op", payload["error"])
        self.assertIn("body", payload["error"])

    def test_slim_response_without_body_field_succeeds(self):
        # Real Voog PUT responses are slim — `body` is omitted entirely.
        # Don't false-positive: detector must fall through.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5}
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "body": "<h1>x</h1>"},
            client,
        )
        self.assertFalse(getattr(result, "isError", False))

    def test_empty_body_input_does_not_trip_detector(self):
        # If the sent body itself was empty, a cleared response is
        # consistent — not a silent no-op symptom. (Body required by
        # earlier validation but defense-in-depth shouldn't false-fire.)
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5, "title": "t"}
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "title": "t"},
            client,
        )
        self.assertFalse(getattr(result, "isError", False))


class TestLayoutDelete(unittest.TestCase):
    def test_layout_delete_description_documents_api_block(self):
        tool = next(t for t in layouts_tools.get_tools() if t.name == "layout_delete")
        desc = tool.description
        # Describes the API behaviour (block) — not a 500 contingency.
        self.assertIn("block", desc.lower())
        self.assertNotIn("500", desc)
        self.assertIn("page_set_layout", desc)

    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool("layout_delete", {"layout_id": 5}, client)
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


class TestLayoutDeleteBlockingPagesPreflight(unittest.TestCase):
    """S15 — on 422, layout_delete fetches blocking pages and surfaces them."""

    def test_422_surfaces_blocking_pages(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.delete.side_effect = urllib.error.HTTPError(
            "https://example.com/admin/api/layouts/7",
            422,
            "layout has assigned pages",
            {},
            None,
        )
        client.get_all.return_value = [
            {"id": 11, "title": "About", "path": "/about", "layout_id": 7},
            {"id": 12, "title": "Contact", "path": "/contact", "layout_id": 7},
        ]
        result = layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 7, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        # Original Voog error preserved.
        self.assertIn("422", payload["error"])
        # Blocking pages surfaced.
        self.assertIn("blocking_pages", payload)
        self.assertEqual(len(payload["blocking_pages"]), 2)
        first = payload["blocking_pages"][0]
        self.assertEqual(first["id"], 11)
        self.assertEqual(first["title"], "About")
        self.assertEqual(first["path"], "/about")
        # Pre-flight GET used the S8 filter hatch.
        client.get_all.assert_called_once_with(
            "/pages",
            params={"q.page.layout_id.$eq": 7},
        )

    def test_other_errors_no_preflight(self):
        # On a non-422 error (e.g. 500), the wrapper must NOT do the
        # pre-flight GET — surface the original error verbatim.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.delete.side_effect = urllib.error.HTTPError("url", 500, "boom", {}, None)
        result = layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 7, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        client.get_all.assert_not_called()
        payload = json.loads(result.content[0].text)
        self.assertNotIn("blocking_pages", payload)

    def test_preflight_get_failure_does_not_mask_original_error(self):
        # If the pre-flight GET itself fails, the original 422 must still
        # be the primary error message — pre-flight is best-effort.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.delete.side_effect = urllib.error.HTTPError(
            "url", 422, "layout has assigned pages", {}, None
        )
        client.get_all.side_effect = urllib.error.HTTPError(
            "url", 503, "Service Unavailable", {}, None
        )
        result = layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 7, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("422", payload["error"])
        # blocking_pages absent (pre-flight failed) — but no crash.
        self.assertNotIn("blocking_pages", payload)

    def test_success_path_unchanged(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.delete.return_value = None
        result = layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 7, "force": True},
            client,
        )
        self.assertFalse(getattr(result, "isError", False))
        client.delete.assert_called_once_with("/layouts/7")
        client.get_all.assert_not_called()


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

    def test_silent_no_op_data_echo_back_empty(self):
        # Defense-in-depth (#99) for the asset path — same symptom that
        # bit `voog push` for legacy "layout_asset" type entries.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 99, "data": ""}
        result = layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "body{margin:0}"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("silent no-op", payload["error"])
        self.assertIn("data", payload["error"])

    def test_slim_response_without_data_field_succeeds(self):
        # Real Voog PUT responses omit `data` entirely — must fall through.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 99, "size": 14}
        result = layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "body{margin:0}"},
            client,
        )
        self.assertFalse(getattr(result, "isError", False))


class TestLayoutAssetDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool("layout_asset_delete", {"asset_id": 99}, client)
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


class TestDecodedEscapeGuard(unittest.TestCase):
    """Issue #138 — refuse content that shows transport-decoded escapes.

    A literal ``\\uXXXX`` in a source file only survives the JSON boundary if
    it was doubled; otherwise the tool receives the decoded character and
    Voog stores that under a clean ✓. U+2028 / U+2029 and raw C0 controls are
    the fingerprints worth refusing: minifiers escape them out of JS source
    precisely because raw they change what the file means.

    Every marker is written here as a Python escape, never as a raw literal —
    an invisible character in a test file is its own foot-gun.
    """

    def _call(self, name, args):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 99}
        client.post.return_value = {"id": 99}
        result = layouts_tools.call_tool(name, args, client)
        return result, client

    def test_asset_update_rejects_line_separator(self):
        result, client = self._call(
            "layout_asset_update",
            {"asset_id": 99, "data": "var dash = '\u2028';"},
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("U+2028", payload["error"])
        self.assertIn("layouts_push", payload["error"])
        # Nothing may reach Voog — refusing after the PUT would be pointless.
        self.assertEqual(client.put.call_count, 0)

    def test_asset_update_rejects_paragraph_separator(self):
        result, client = self._call(
            "layout_asset_update",
            {"asset_id": 99, "data": "a\u2029b"},
        )
        self.assertTrue(result.isError)
        self.assertIn("U+2029", json.loads(result.content[0].text)["error"])
        self.assertEqual(client.put.call_count, 0)

    def test_asset_update_rejects_c0_control(self):
        result, client = self._call(
            "layout_asset_update",
            {"asset_id": 99, "data": "console.log('\u0007');"},
        )
        self.assertTrue(result.isError)
        self.assertIn("U+0007", json.loads(result.content[0].text)["error"])
        self.assertEqual(client.put.call_count, 0)

    def test_asset_update_allows_ordinary_text(self):
        # The guard must not fire on the content people actually push:
        # Estonian letters, typographic dashes, emoji, tabs and newlines.
        # An en-dash IS what a decoded – looks like — but it is also a
        # character authors type directly, so refusing it would break real
        # pushes to catch a cosmetic diff.
        payload_text = "/* õäöü – — ✓ 🇪🇪 */\n\tvar x = 1;\r\n"
        result, client = self._call(
            "layout_asset_update",
            {"asset_id": 99, "data": payload_text},
        )
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(client.put.call_args.args[1], {"data": payload_text})

    def test_asset_create_rejects_line_separator(self):
        result, client = self._call(
            "layout_asset_create",
            {"filename": "app.js", "asset_type": "javascript", "data": "x\u2028y"},
        )
        self.assertTrue(result.isError)
        self.assertIn("U+2028", json.loads(result.content[0].text)["error"])
        self.assertEqual(client.post.call_count, 0)

    def test_layout_update_body_rejects_line_separator(self):
        result, client = self._call(
            "layout_update",
            {"layout_id": 42, "body": "{% if x %}\u2028{% endif %}"},
        )
        self.assertTrue(result.isError)
        self.assertIn("U+2028", json.loads(result.content[0].text)["error"])
        self.assertEqual(client.put.call_count, 0)

    def test_layout_update_title_only_unaffected(self):
        # The guard is scoped to content fields; a title-only update must
        # still work exactly as before.
        result, client = self._call("layout_update", {"layout_id": 42, "title": "Uus nimi"})
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(client.put.call_args.args[1], {"title": "Uus nimi"})

    def test_source_file_carries_no_raw_separators(self):
        # The module warns about invisible characters — it must not contain
        # any itself. Guards against a future edit pasting one in.
        from pathlib import Path

        from voog.mcp.tools import layouts as layouts_tools

        source = Path(layouts_tools.__file__).read_text(encoding="utf-8")
        self.assertNotIn("\u2028", source)
        self.assertNotIn("\u2029", source)


class TestLayoutAssetUpload(unittest.TestCase):
    """Issue #140 item 4 — binary layout assets (favicons, fonts, icons).

    layout_asset_create carries text `data` only, so binaries previously
    needed a raw curl call. Verified live: multipart POST returns
    asset_type=image, editable=false.
    """

    def _png(self, tmpdir, name="favicon.png"):
        from pathlib import Path as _P

        path = _P(tmpdir) / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return str(path)

    def test_posts_multipart_with_derived_content_type(self):
        import tempfile

        client = MagicMock()
        client.post_file.return_value = {"id": 2642542, "filename": "favicon.png"}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._png(tmp)
            result = layouts_tools.call_tool("layout_asset_upload", {"file_path": path}, client)
        kwargs = client.post_file.call_args.kwargs
        self.assertEqual(client.post_file.call_args.args[0], "/layout_assets")
        self.assertEqual(kwargs["filename"], "favicon.png")
        self.assertEqual(kwargs["content_type"], "image/png")
        self.assertTrue(kwargs["content"].startswith(b"\x89PNG"))
        self.assertIn("2642542", result[0].text)

    def test_filename_override(self):
        import tempfile

        client = MagicMock()
        client.post_file.return_value = {"id": 1}
        with tempfile.TemporaryDirectory() as tmp:
            layouts_tools.call_tool(
                "layout_asset_upload",
                {"file_path": self._png(tmp), "filename": "site-icon.png"},
                client,
            )
        self.assertEqual(client.post_file.call_args.kwargs["filename"], "site-icon.png")

    def test_text_asset_extension_points_at_the_right_tool(self):
        import tempfile

        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._png(tmp, "styles.css")
            result = layouts_tools.call_tool("layout_asset_upload", {"file_path": path}, client)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("layout_asset_create", payload["error"])
        self.assertIn("layouts_push", payload["error"])
        client.post_file.assert_not_called()

    def test_relative_path_and_missing_file_rejected(self):
        client = MagicMock()
        for args in ({"file_path": "icons/favicon.png"}, {"file_path": "/nope/favicon.png"}):
            result = layouts_tools.call_tool("layout_asset_upload", args, client)
            self.assertTrue(result.isError)
        client.post_file.assert_not_called()

    def test_filename_with_slash_rejected(self):
        import tempfile

        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            result = layouts_tools.call_tool(
                "layout_asset_upload",
                {"file_path": self._png(tmp), "filename": "icons/favicon.png"},
                client,
            )
        self.assertTrue(result.isError)
        client.post_file.assert_not_called()


class TestEscapeGuardWhitespaceTolerance(unittest.TestCase):
    """The guard must not fire on whitespace that real sources contain.

    Vertical tab and form feed are legal whitespace in both CSS and JS, and
    ^L page breaks appear in hand-written files. Refusing them would be a
    false positive on ordinary content — the guard exists for characters
    that are never authored raw.
    """

    def test_vertical_tab_and_form_feed_are_allowed(self):
        client = MagicMock()
        client.put.return_value = {"id": 1}
        body = "/* page one */\u000c.a{color:red}\u000b"
        result = layouts_tools.call_tool(
            "layout_asset_update", {"asset_id": 1, "data": body}, client
        )
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(client.put.call_args.args[1], {"data": body})

    def test_every_guarded_tool_documents_the_json_boundary(self):
        # The changelog claims all three say so up front; an LLM reads the
        # description, not the source, so the claim has to be true.
        tools = {t.name: t for t in layouts_tools.get_tools()}
        for name in ("layout_update", "layout_asset_create", "layout_asset_update"):
            self.assertIn("#138", tools[name].description, f"{name} omits the caveat")

    def test_layout_create_body_is_guarded(self):
        # The one route that puts a brand-new layout body on the site from
        # an MCP string argument — same corruption class as the rest.
        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_create",
            {"title": "T", "kind": "layout", "body": "{% if x %}\u2028{% endif %}"},
            client,
        )
        self.assertTrue(result.isError)
        self.assertIn("U+2028", json.loads(result.content[0].text)["error"])
        client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
