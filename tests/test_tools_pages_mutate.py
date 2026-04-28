"""Tests for voog_mcp.tools.pages_mutate."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._test_helpers import _ann_get
from voog_mcp.tools import pages_mutate as pages_mutate_tools


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_three(self):
        tools = pages_mutate_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["page_set_hidden", "page_set_layout", "page_delete"])

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
        self.assertIs(
            _ann_get(ann, "destructiveHint", "destructive_hint"), True
        )
        self.assertIs(
            _ann_get(ann, "readOnlyHint", "read_only_hint"), False
        )
        # Deleting twice returns 404 the second time — different effect → not idempotent
        self.assertIs(
            _ann_get(ann, "idempotentHint", "idempotent_hint"), False
        )


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
            "voog_mcp.tools.pages_mutate.parallel_map",
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
        client.put.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
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
        client.delete.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
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
        result = pages_mutate_tools.call_tool(
            "nonexistent", {}, client
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — pages_mutate joined to TOOL_GROUPS."""

    def test_pages_mutate_in_tool_groups(self):
        from voog_mcp import server
        self.assertIn(pages_mutate_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        # All tool names across all groups must be unique
        from voog_mcp import server
        all_names = [
            tool.name
            for group in server.TOOL_GROUPS
            for tool in group.get_tools()
        ]
        self.assertEqual(len(all_names), len(set(all_names)),
                         f"Duplicate tool names: {all_names}")


if __name__ == "__main__":
    unittest.main()
