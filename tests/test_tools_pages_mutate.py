"""Tests for voog.mcp.tools.pages_mutate."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from tests._test_helpers import _ann_get
from voog.mcp.tools import pages_mutate as pages_mutate_tools


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_eight(self):
        tools = pages_mutate_tools.get_tools()
        names = sorted(t.name for t in tools)
        self.assertEqual(
            names,
            sorted(
                [
                    "page_set_hidden",
                    "page_set_layout",
                    "page_delete",
                    "page_create",
                    "page_update",
                    "page_set_data",
                    "page_delete_data",
                    "page_duplicate",
                ]
            ),
        )

    def test_page_set_hidden_schema(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        schema = tools["page_set_hidden"].inputSchema
        self.assertEqual(schema["properties"]["ids"]["type"], "array")
        self.assertEqual(schema["properties"]["ids"]["items"]["type"], "integer")
        self.assertEqual(schema["properties"]["hidden"]["type"], "boolean")
        self.assertIn("ids", schema["required"])
        self.assertIn("hidden", schema["required"])

    def test_page_set_layout_schema(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        schema = tools["page_set_layout"].inputSchema
        self.assertEqual(schema["properties"]["page_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["layout_id"]["type"], "integer")
        self.assertIn("page_id", schema["required"])
        self.assertIn("layout_id", schema["required"])

    def test_page_delete_schema(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        schema = tools["page_delete"].inputSchema
        self.assertEqual(schema["properties"]["page_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["force"]["type"], "boolean")
        self.assertIn("page_id", schema["required"])
        # force is optional with default false (defensive opt-in)
        self.assertNotIn("force", schema["required"])

    def test_page_delete_has_destructive_hint(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        ann = tools["page_delete"].annotations
        self.assertTrue(_ann_get(ann, "destructiveHint", "destructive_hint"))

    def test_page_delete_data_has_destructive_hint(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        ann = tools["page_delete_data"].annotations
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), True)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)

    def test_setters_have_explicit_non_destructive_annotations(self):
        # set_hidden / set_layout are reversible mutations. MCP spec defaults
        # destructiveHint to true when readOnlyHint is false — so we MUST
        # explicitly set destructiveHint=False (not omit it) for clients to
        # treat these as safe-to-call without confirmation.
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        for name in ("page_set_hidden", "page_set_layout"):
            ann = tools[name].annotations
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"),
                False,
                f"{name} must have destructiveHint=False explicitly (spec default is True)",
            )
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"),
                False,
                f"{name} should set readOnlyHint=False explicitly",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"),
                True,
                f"{name} should set idempotentHint=True (repeat calls = same end state)",
            )

    def test_page_delete_has_full_explicit_annotations(self):
        tools = {t.name: t for t in pages_mutate_tools.get_tools()}
        ann = tools["page_delete"].annotations
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), True)
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        # Deleting twice returns 404 the second time — different effect → not idempotent
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)


class TestPageSetHidden(unittest.TestCase):
    def test_single_page_success(self):
        client = MagicMock()
        client.put.return_value = {"id": 152377, "hidden": True}
        result = pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": [152377], "hidden": True},
            client,
        )
        client.put.assert_called_once_with("/pages/152377", {"hidden": True})
        # success_response with summary → 2 TextContents
        self.assertEqual(len(result), 2)

    def test_bulk_calls_per_id(self):
        client = MagicMock()
        client.put.return_value = {}
        ids = [1, 2, 3]
        pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": ids, "hidden": False},
            client,
        )
        # 3 PUT calls
        self.assertEqual(client.put.call_count, 3)
        for i, call in enumerate(client.put.call_args_list):
            args, kwargs = call
            self.assertEqual(args[0], f"/pages/{ids[i]}")
            self.assertEqual(args[1], {"hidden": False})

    def test_partial_failure_reports_per_id_status(self):
        client = MagicMock()

        # ids 1 and 3 succeed; id 2 fails. Keyed by path so success/failure
        # binding stays invariant under ThreadPoolExecutor's non-deterministic
        # call order (max_workers=4).
        def put_dispatch(path, body, **kwargs):
            if path == "/pages/1":
                return {"id": 1, "hidden": True}
            if path == "/pages/2":
                raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)
            if path == "/pages/3":
                return {"id": 3, "hidden": True}
            raise AssertionError(f"unexpected path: {path}")

        client.put.side_effect = put_dispatch
        result = pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": [1, 2, 3], "hidden": True},
            client,
        )
        # Bulk result is structured: returns 2 TextContents (summary + JSON breakdown)
        self.assertEqual(len(result), 2)
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 3)
        self.assertEqual(breakdown["succeeded"], 2)
        self.assertEqual(breakdown["failed"], 1)
        # Per-id detail with the failure id captured
        results = breakdown["results"]
        self.assertEqual(len(results), 3)
        ok_ids = [r["id"] for r in results if r["ok"]]
        fail_ids = [r["id"] for r in results if not r["ok"]]
        self.assertEqual(sorted(ok_ids), [1, 3])
        self.assertEqual(fail_ids, [2])

    def test_all_failures_still_returns_breakdown(self):
        client = MagicMock()

        # Both ids fail. Keyed by path (rather than positional list) so the
        # binding is invariant under ThreadPoolExecutor scheduling.
        def put_dispatch(path, body, **kwargs):
            if path in ("/pages/10", "/pages/20"):
                raise urllib.error.URLError("network down")
            raise AssertionError(f"unexpected path: {path}")

        client.put.side_effect = put_dispatch
        result = pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": [10, 20], "hidden": True},
            client,
        )
        # Even all-failures path returns success_response with the breakdown
        # (the operation itself completed; per-id results communicate failures)
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["succeeded"], 0)
        self.assertEqual(breakdown["failed"], 2)

    def test_empty_ids_returns_error(self):
        client = MagicMock()
        result = pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": [], "hidden": True},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_uses_parallel_map_with_max_workers_four(self):
        # Spec § 4.3: writes use max_workers=4 (more conservative than reads).
        client = MagicMock()
        client.put.return_value = {}
        with patch(
            "voog.mcp.tools.pages_mutate.parallel_map",
            wraps=pages_mutate_tools.parallel_map,
        ) as wrapped:
            pages_mutate_tools.call_tool(
                "page_set_hidden",
                {"ids": [1, 2, 3], "hidden": True},
                client,
            )
        wrapped.assert_called_once()
        # Validate max_workers=4 was passed (kwarg, not positional)
        _args, kwargs = wrapped.call_args
        self.assertEqual(kwargs.get("max_workers"), 4)

    def test_one_of_n_put_failure_isolated(self):
        # 1-of-N failure: that id reports ok=False with error; others ok=True.
        client = MagicMock()

        def put_side_effect(path, body):
            if path == "/pages/2":
                raise urllib.error.HTTPError("u", 500, "boom", {}, None)
            return {}

        client.put.side_effect = put_side_effect
        result = pages_mutate_tools.call_tool(
            "page_set_hidden",
            {"ids": [1, 2, 3, 4], "hidden": True},
            client,
        )
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 4)
        self.assertEqual(breakdown["succeeded"], 3)
        self.assertEqual(breakdown["failed"], 1)
        results_by_id = {r["id"]: r for r in breakdown["results"]}
        self.assertTrue(results_by_id[1]["ok"])
        self.assertFalse(results_by_id[2]["ok"])
        self.assertIn("error", results_by_id[2])
        self.assertTrue(results_by_id[3]["ok"])
        self.assertTrue(results_by_id[4]["ok"])
        # Order preserved in results list (matches input ids order)
        self.assertEqual([r["id"] for r in breakdown["results"]], [1, 2, 3, 4])


class TestPageSetLayout(unittest.TestCase):
    def test_success_calls_put(self):
        client = MagicMock()
        client.put.return_value = {"id": 152377, "layout_id": 977702}
        result = pages_mutate_tools.call_tool(
            "page_set_layout",
            {"page_id": 152377, "layout_id": 977702},
            client,
        )
        client.put.assert_called_once_with("/pages/152377", {"layout_id": 977702})
        self.assertEqual(len(result), 2)  # summary + JSON

    def test_api_error_returns_error_response(self):
        client = MagicMock()
        client.put.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        result = pages_mutate_tools.call_tool(
            "page_set_layout",
            {"page_id": 999, "layout_id": 1},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("page_set_layout", payload["error"])


class TestPageDelete(unittest.TestCase):
    def test_force_false_rejected(self):
        client = MagicMock()
        result = pages_mutate_tools.call_tool(
            "page_delete",
            {"page_id": 152377, "force": False},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("force", payload["error"])

    def test_force_omitted_rejected(self):
        # Defensive default: force defaults to false → delete blocked
        client = MagicMock()
        result = pages_mutate_tools.call_tool(
            "page_delete",
            {"page_id": 152377},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_force_true_calls_delete(self):
        client = MagicMock()
        client.delete.return_value = None  # API returns 204 No Content
        result = pages_mutate_tools.call_tool(
            "page_delete",
            {"page_id": 152377, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/pages/152377")
        # Returns success summary (no body to JSON-encode)
        self.assertGreaterEqual(len(result), 1)
        # The summary text mentions the deleted id
        summary_text = result[0].text
        self.assertIn("152377", summary_text)

    def test_force_true_api_error_returns_error_response(self):
        client = MagicMock()
        client.delete.side_effect = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        result = pages_mutate_tools.call_tool(
            "page_delete",
            {"page_id": 999, "force": True},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("page_delete", payload["error"])


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = MagicMock()
        result = pages_mutate_tools.call_tool("nonexistent", {}, client)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — pages_mutate joined to TOOL_GROUPS."""

    def test_pages_mutate_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(pages_mutate_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        # All tool names across all groups must be unique
        from voog.mcp import server

        all_names = [tool.name for group in server.TOOL_GROUPS for tool in group.get_tools()]
        self.assertEqual(len(all_names), len(set(all_names)), f"Duplicate tool names: {all_names}")


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import pages_mutate as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


class TestPageCreate(unittest.TestCase):
    def test_minimal_root_page(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 100, "path": "uus-leht"}
        pm.call_tool(
            "page_create",
            {
                "title": "Uus leht",
                "slug": "uus-leht",
                "language_id": 627583,
            },
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/pages")
        # Per skill: root pages omit parent_id (Voog attaches to root node)
        self.assertNotIn("parent_id", body)
        self.assertEqual(body["title"], "Uus leht")
        self.assertEqual(body["language_id"], 627583)

    def test_subpage_with_parent_id(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 100}
        pm.call_tool(
            "page_create",
            {
                "title": "Sub",
                "slug": "sub",
                "language_id": 627583,
                "parent_id": 5,
                "layout_id": 7,
                "hidden": True,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["parent_id"], 5)
        self.assertEqual(body["layout_id"], 7)
        self.assertIs(body["hidden"], True)

    def test_parallel_translation_with_node_id(self):
        # Per skill memory: second-language page must use node_id of the
        # first-language page, NOT parent_id, so the two are parallels.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 200}
        pm.call_tool(
            "page_create",
            {
                "title": "Coloured totes",
                "slug": "coloured-totes",
                "language_id": 627582,
                "node_id": 999,
                "layout_id": 7,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["node_id"], 999)
        self.assertNotIn("parent_id", body)

    def test_node_id_and_parent_id_mutually_exclusive(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_create",
            {
                "title": "X",
                "slug": "x",
                "language_id": 1,
                "node_id": 5,
                "parent_id": 9,
            },
            client,
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()

    def test_page_create_rejects_invalid_content_type(self):
        # A typo'd content_type should fail loudly client-side with the
        # known-good set listed in the error, rather than let Voog return
        # a generic 422.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_create",
            {
                "title": "X",
                "slug": "x",
                "language_id": 1,
                "content_type": "bloog",
            },
            client,
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("content_type", payload["error"])
        # Error must list the known-good values so the LLM can self-correct
        self.assertIn("page", payload["error"])

    def test_page_create_accepts_each_valid_content_type(self):
        # Regression: looping the whitelist must succeed for every value.
        from voog.mcp.tools import pages_mutate as pm
        from voog.mcp.tools.pages_mutate import VALID_PAGE_CONTENT_TYPES

        # Sanity: the whitelist exists, is non-empty, and includes the
        # documented core values.
        self.assertGreater(len(VALID_PAGE_CONTENT_TYPES), 0)
        for required in ("page", "link", "blog", "product"):
            self.assertIn(required, VALID_PAGE_CONTENT_TYPES)

        for ct in VALID_PAGE_CONTENT_TYPES:
            client = MagicMock()
            client.post.return_value = {"id": 1}
            result = pm.call_tool(
                "page_create",
                {
                    "title": "X",
                    "slug": "x",
                    "language_id": 1,
                    "content_type": ct,
                },
                client,
            )
            # Not an error — and the POST was actually issued
            self.assertNotEqual(
                getattr(result, "isError", False),
                True,
                f"content_type={ct!r} should be accepted but was rejected",
            )
            client.post.assert_called_once()


class TestPageUpdate(unittest.TestCase):
    def test_update_title_and_slug(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {"page_id": 5, "title": "Uus", "slug": "uus"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/pages/5")
        self.assertEqual(body["title"], "Uus")
        self.assertEqual(body["slug"], "uus")

    def test_update_layout_and_image(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {"page_id": 5, "layout_id": 7, "image_id": 1234},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["layout_id"], 7)
        self.assertEqual(body["image_id"], 1234)

    def test_update_keywords_description(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {
                "page_id": 5,
                "keywords": "kuju, voog",
                "description": "Meta description",
                "content_type": "page",
            },
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["keywords"], "kuju, voog")
        self.assertEqual(body["description"], "Meta description")
        self.assertEqual(body["content_type"], "page")

    def test_update_rejects_empty(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool("page_update", {"page_id": 5}, client)
        self.assertTrue(result.isError)

    def test_page_update_rejects_self_parent_cycle(self):
        # parent_id == page_id would create a self-referential parent and
        # cycle through Voog's tree walker. Match the mutex pattern in
        # _page_create — surface a clear local error rather than letting
        # Voog return a generic 422.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_update",
            {"page_id": 5, "parent_id": 5},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("parent_id", payload["error"])


class TestPageSetData(unittest.TestCase):
    def test_set_single_data_key(self):
        # Voog: PUT /pages/{id}/data/{key}, body {"value": "..."}
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"key": "foo", "value": "bar"}
        pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "foo", "value": "bar"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/pages/5/data/foo")
        self.assertEqual(body, {"value": "bar"})

    def test_rejects_internal_prefix(self):
        # Voog protects keys starting with internal_ — surface this with
        # a clear error rather than letting the API 422.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "internal_secret", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)

    def test_set_data_rejects_slash_in_key(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "foo/bar", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_set_data_rejects_question_mark_in_key(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "foo?x=1", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_set_data_rejects_percent_encoded_traversal(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "%2e%2e", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestPageDeleteData(unittest.TestCase):
    def test_requires_force(self):
        # Without force=True the call must be rejected and DELETE not called.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_delete_data",
            {"page_id": 5, "key": "foo"},
            client,
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        pm.call_tool(
            "page_delete_data",
            {"page_id": 5, "key": "foo", "force": True},
            client,
        )
        client.delete.assert_called_once_with("/pages/5/data/foo")


class TestPageDuplicate(unittest.TestCase):
    def test_duplicate(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 200, "path": "copy"}
        pm.call_tool("page_duplicate", {"page_id": 5}, client)
        client.post.assert_called_once_with("/pages/5/duplicate", {})

    def test_page_duplicate_summary_includes_hidden_flag(self):
        # Voog returns the duplicated page with hidden=True by default. The
        # success summary should surface that so the LLM caller knows it
        # needs page_set_hidden(false) before the page is publicly visible.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 200, "path": "copy", "hidden": True}
        result = pm.call_tool("page_duplicate", {"page_id": 5}, client)
        # success_response returns a list of TextContent when summary is set;
        # the first entry is the summary string.
        summary_text = result[0].text
        self.assertIn("hidden", summary_text)
        self.assertIn("page_set_hidden", summary_text)


if __name__ == "__main__":
    unittest.main()
