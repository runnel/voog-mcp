"""Tests for voog_mcp.tools.layouts_sync — layouts_pull + layouts_push."""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._test_helpers import _ann_get
from voog_mcp.tools import layouts_sync as layouts_sync_tools


def _normalize_type(t):
    """Allow either string 'array' or list ['array', 'null'] in JSON Schema."""
    if isinstance(t, list):
        return set(t)
    return {t}


def _make_client():
    client = MagicMock()
    client.host = "test.example.com"
    client.ecommerce_url = "https://test.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_two(self):
        tools = layouts_sync_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["layouts_pull", "layouts_push"])

    def test_layouts_pull_schema(self):
        tools = {t.name: t for t in layouts_sync_tools.get_tools()}
        schema = tools["layouts_pull"].inputSchema
        self.assertEqual(schema["properties"]["target_dir"]["type"], "string")
        self.assertIn("target_dir", schema["required"])

    def test_layouts_push_schema(self):
        tools = {t.name: t for t in layouts_sync_tools.get_tools()}
        schema = tools["layouts_push"].inputSchema
        self.assertEqual(schema["properties"]["target_dir"]["type"], "string")
        self.assertIn("target_dir", schema["required"])
        # files is optional, defaults to null/all
        self.assertNotIn("files", schema["required"])
        files_prop = schema["properties"]["files"]
        # array of strings, nullable
        self.assertIn("array", _normalize_type(files_prop["type"]))

    def test_both_tools_have_full_explicit_annotations(self):
        # Same as snapshot tools — disk-write but additive (push mutates Voog API
        # but produces the same end state for the same input → idempotent).
        tools = layouts_sync_tools.get_tools()
        for tool in tools:
            ann = tool.annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"), False,
                f"{tool.name} writes to disk and/or API → readOnlyHint=False",
            )
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"), False,
                f"{tool.name} is non-destructive (no data loss)",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"), True,
                f"{tool.name} is idempotent (same input → same end state)",
            )


class TestLayoutsPull(unittest.TestCase):
    def test_writes_layouts_components_and_manifest(self):
        client = _make_client()
        # /layouts list
        client.get_all.return_value = [
            {"id": 100, "title": "default", "component": False, "updated_at": "2026-01-01T00:00:00Z"},
            {"id": 200, "title": "header", "component": True, "updated_at": "2026-01-02T00:00:00Z"},
        ]
        # per-id detail with body
        client.get.side_effect = [
            {"id": 100, "title": "default", "body": "<html>{{ content }}</html>"},
            {"id": 200, "title": "header", "body": "<nav>...</nav>"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            result = layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            # Layout file in layouts/
            self.assertTrue((target / "layouts" / "default.tpl").exists())
            self.assertEqual(
                (target / "layouts" / "default.tpl").read_text(encoding="utf-8"),
                "<html>{{ content }}</html>",
            )
            # Component file in components/
            self.assertTrue((target / "components" / "header.tpl").exists())
            self.assertEqual(
                (target / "components" / "header.tpl").read_text(encoding="utf-8"),
                "<nav>...</nav>",
            )
            # Manifest
            manifest_path = target / "manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Match voog.py shape: {<rel_path>: {id, type, updated_at}}
            self.assertEqual(
                manifest["layouts/default.tpl"],
                {"id": 100, "type": "layout", "updated_at": "2026-01-01T00:00:00Z"},
            )
            self.assertEqual(
                manifest["components/header.tpl"],
                {"id": 200, "type": "layout", "updated_at": "2026-01-02T00:00:00Z"},
            )

        # Result has summary + breakdown
        self.assertEqual(len(result), 2)
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["layouts_written"], 1)
        self.assertEqual(breakdown["components_written"], 1)
        self.assertTrue(breakdown["manifest_path"].endswith("manifest.json"))

    def test_refuses_existing_dir_with_tpl_files(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "existing"
            (target / "layouts").mkdir(parents=True)
            (target / "layouts" / "stale.tpl").write_text("OLD", encoding="utf-8")
            result = layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            client.get_all.assert_not_called()
            client.get.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            # Stale file untouched
            self.assertEqual(
                (target / "layouts" / "stale.tpl").read_text(encoding="utf-8"),
                "OLD",
            )

    def test_allows_existing_empty_dir(self):
        # Existing dir without .tpl files is fine — caller may want to refresh
        # into a known-empty location (e.g. clean checkout).
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "title": "x", "component": False, "updated_at": ""},
        ]
        client.get.return_value = {"id": 1, "body": "x-body"}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "fresh"
            target.mkdir(parents=True)  # exists but empty — OK
            result = layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            self.assertTrue((target / "layouts" / "x.tpl").exists())
            payload = json.loads(result[1].text)
            self.assertEqual(payload["layouts_written"], 1)

    def test_allows_existing_dir_with_non_tpl_files(self):
        # Dir with .gitignore, README.md, etc. is fine — only .tpl files block
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "title": "x", "component": False, "updated_at": ""},
        ]
        client.get.return_value = {"id": 1, "body": "x-body"}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "with_other"
            target.mkdir(parents=True)
            (target / "README.md").write_text("docs", encoding="utf-8")
            layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            self.assertTrue((target / "layouts" / "x.tpl").exists())
            self.assertTrue((target / "README.md").exists())  # not clobbered

    def test_creates_parent_dirs(self):
        client = _make_client()
        client.get_all.return_value = []
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "deep" / "nested" / "tree"
            layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            self.assertTrue((target / "manifest.json").exists())

    def test_api_failure_returns_error(self):
        client = _make_client()
        client.get_all.side_effect = urllib.error.URLError("network down")
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            result = layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn("layouts_pull", payload["error"])

    def test_per_layout_detail_failure_continues(self):
        # Single layout 404 → per-layout error, others still written
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "title": "ok", "component": False, "updated_at": ""},
            {"id": 2, "title": "bad", "component": False, "updated_at": ""},
            {"id": 3, "title": "ok2", "component": False, "updated_at": ""},
        ]
        client.get.side_effect = [
            {"id": 1, "body": "ok-body"},
            urllib.error.HTTPError("u", 404, "Not Found", {}, None),
            {"id": 3, "body": "ok2-body"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            result = layouts_sync_tools.call_tool(
                "layouts_pull", {"target_dir": str(target)}, client,
            )
            self.assertTrue((target / "layouts" / "ok.tpl").exists())
            self.assertFalse((target / "layouts" / "bad.tpl").exists())
            self.assertTrue((target / "layouts" / "ok2.tpl").exists())
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["layouts_written"], 2)
        self.assertEqual(len(breakdown["per_layout_errors"]), 1)
        self.assertEqual(breakdown["per_layout_errors"][0]["layout_id"], 2)

    def test_empty_target_dir_rejected(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "layouts_pull", {"target_dir": ""}, client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_relative_path_rejected(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "layouts_pull", {"target_dir": "tree/foo"}, client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("absolute path", payload["error"])

    def test_dot_relative_path_rejected(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "layouts_pull", {"target_dir": "./tree"}, client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute path", payload["error"])


def _make_pulled_tree(target: Path, manifest: dict, contents: dict):
    """Materialize a fake pulled tree: manifest.json + per-rel-path files.

    contents is {rel_path: text}; manifest is {rel_path: {id, type, updated_at}}.
    """
    target.mkdir(parents=True, exist_ok=True)
    for rel_path, text in contents.items():
        full = target / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )


class TestLayoutsPush(unittest.TestCase):
    def test_pushes_all_when_files_null(self):
        client = _make_client()
        client.put.return_value = {}  # PUT body irrelevant
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/default.tpl": {"id": 100, "type": "layout", "updated_at": ""},
                    "components/header.tpl": {"id": 200, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/default.tpl": "<html>v2</html>",
                    "components/header.tpl": "<nav>v2</nav>",
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target), "files": None}, client,
            )
        # 2 PUT calls
        self.assertEqual(client.put.call_count, 2)
        calls = sorted([
            (c.args[0], c.args[1]["body"]) if len(c.args) > 1 else (c.args[0], c.kwargs["data"]["body"])
            for c in client.put.call_args_list
        ])
        self.assertEqual(calls, [
            ("/layouts/100", "<html>v2</html>"),
            ("/layouts/200", "<nav>v2</nav>"),
        ])
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 2)
        self.assertEqual(breakdown["succeeded"], 2)
        self.assertEqual(breakdown["failed"], 0)

    def test_pushes_only_specified_files(self):
        client = _make_client()
        client.put.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/a.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                    "layouts/b.tpl": {"id": 2, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/a.tpl": "A",
                    "layouts/b.tpl": "B",
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push",
                {"target_dir": str(target), "files": ["layouts/a.tpl"]},
                client,
            )
        # Only /layouts/1 pushed
        self.assertEqual(client.put.call_count, 1)
        args = client.put.call_args
        self.assertEqual(args.args[0], "/layouts/1")
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 1)
        self.assertEqual(breakdown["succeeded"], 1)

    def test_missing_manifest_returns_error(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            target.mkdir(parents=True)
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target)}, client,
            )
            client.put.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn("manifest", payload["error"])

    def test_missing_file_in_manifest_captured_per_file(self):
        # Manifest references a file that doesn't exist on disk — captured
        # in per-file breakdown, doesn't abort the rest of the push.
        client = _make_client()
        client.put.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/exists.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                    "layouts/missing.tpl": {"id": 2, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/exists.tpl": "exists-body",
                    # missing.tpl intentionally NOT created
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target)}, client,
            )
        # Only one PUT (the existing file)
        self.assertEqual(client.put.call_count, 1)
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 2)
        self.assertEqual(breakdown["succeeded"], 1)
        self.assertEqual(breakdown["failed"], 1)
        # Per-file failure entry mentions the missing path
        failed_entries = [r for r in breakdown["results"] if not r["ok"]]
        self.assertEqual(len(failed_entries), 1)
        self.assertEqual(failed_entries[0]["file"], "layouts/missing.tpl")

    def test_per_file_put_failure_captured(self):
        client = _make_client()
        # First PUT succeeds, second raises
        client.put.side_effect = [
            {},
            urllib.error.HTTPError("u", 422, "Unprocessable", {}, None),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/a.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                    "layouts/b.tpl": {"id": 2, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/a.tpl": "A",
                    "layouts/b.tpl": "B",
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target)}, client,
            )
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 2)
        self.assertEqual(breakdown["succeeded"], 1)
        self.assertEqual(breakdown["failed"], 1)

    def test_layout_asset_entry_captured_as_failure_not_mis_put(self):
        # voog.py-pulled trees mix type=layout and type=layout_asset entries.
        # Sending an asset id to PUT /layouts/{id} would either 404 or — in
        # the worst case where id-spaces collide — overwrite a real layout's
        # body with a CSS/JS payload. layouts_push must catch this BEFORE
        # the PUT.
        client = _make_client()
        client.put.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/page.tpl": {"id": 100, "type": "layout", "updated_at": ""},
                    "stylesheets/main.css": {
                        "id": 50,
                        "type": "layout_asset",
                        "asset_type": "stylesheet",
                        "updated_at": "",
                    },
                },
                contents={
                    "layouts/page.tpl": "page-body",
                    "stylesheets/main.css": "body { color: red; }",
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target)}, client,
            )
        # Only the layout entry was PUT — asset entry must NOT have been dispatched
        self.assertEqual(client.put.call_count, 1)
        self.assertEqual(client.put.call_args.args[0], "/layouts/100")
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 2)
        self.assertEqual(breakdown["succeeded"], 1)
        self.assertEqual(breakdown["failed"], 1)
        failed = [r for r in breakdown["results"] if not r["ok"]][0]
        self.assertEqual(failed["file"], "stylesheets/main.css")
        self.assertIn("layout_asset", failed["error"])

    def test_missing_file_in_explicit_files_arg_still_captured(self):
        # User passes `files=["typo.tpl"]` that's not in manifest — captured
        # as failure rather than silent skip.
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/a.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                contents={"layouts/a.tpl": "A"},
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push",
                {"target_dir": str(target), "files": ["layouts/typo.tpl"]},
                client,
            )
        client.put.assert_not_called()
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 1)
        self.assertEqual(breakdown["failed"], 1)

    def test_relative_path_rejected(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "layouts_push", {"target_dir": "tree/foo"}, client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute path", payload["error"])

    def test_empty_target_dir_rejected(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "layouts_push", {"target_dir": ""}, client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_uses_parallel_map_with_max_workers_four(self):
        # Spec § 4.3: writes use max_workers=4 (more conservative than reads).
        client = _make_client()
        client.put.return_value = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/a.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                    "layouts/b.tpl": {"id": 2, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/a.tpl": "A",
                    "layouts/b.tpl": "B",
                },
            )
            with patch(
                "voog_mcp.tools.layouts_sync.parallel_map",
                wraps=layouts_sync_tools.parallel_map,
            ) as wrapped:
                layouts_sync_tools.call_tool(
                    "layouts_push", {"target_dir": str(target)}, client,
                )
        wrapped.assert_called_once()
        _args, kwargs = wrapped.call_args
        self.assertEqual(kwargs.get("max_workers"), 4)

    def test_one_of_n_put_failure_isolated(self):
        # 1-of-N PUT failure: that file reports ok=False with error; others ok=True.
        client = _make_client()

        def put_side_effect(path, body):
            if path == "/layouts/2":
                raise urllib.error.HTTPError("u", 500, "boom", {}, None)
            return {}

        client.put.side_effect = put_side_effect
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tree"
            _make_pulled_tree(
                target,
                manifest={
                    "layouts/a.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                    "layouts/b.tpl": {"id": 2, "type": "layout", "updated_at": ""},
                    "layouts/c.tpl": {"id": 3, "type": "layout", "updated_at": ""},
                    "layouts/d.tpl": {"id": 4, "type": "layout", "updated_at": ""},
                },
                contents={
                    "layouts/a.tpl": "A",
                    "layouts/b.tpl": "B",
                    "layouts/c.tpl": "C",
                    "layouts/d.tpl": "D",
                },
            )
            result = layouts_sync_tools.call_tool(
                "layouts_push", {"target_dir": str(target)}, client,
            )
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["total"], 4)
        self.assertEqual(breakdown["succeeded"], 3)
        self.assertEqual(breakdown["failed"], 1)
        results_by_file = {r["file"]: r for r in breakdown["results"]}
        self.assertTrue(results_by_file["layouts/a.tpl"]["ok"])
        self.assertFalse(results_by_file["layouts/b.tpl"]["ok"])
        self.assertEqual(results_by_file["layouts/b.tpl"]["id"], 2)
        self.assertIn("error", results_by_file["layouts/b.tpl"])
        self.assertTrue(results_by_file["layouts/c.tpl"]["ok"])
        self.assertTrue(results_by_file["layouts/d.tpl"]["ok"])


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = _make_client()
        result = layouts_sync_tools.call_tool(
            "nonexistent", {}, client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    def test_layouts_sync_in_tool_groups(self):
        from voog_mcp import server
        self.assertIn(layouts_sync_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
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
