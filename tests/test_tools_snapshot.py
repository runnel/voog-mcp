"""Tests for voog.mcp.tools.snapshot."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from tests._test_helpers import _ann_get
from voog.mcp.tools import snapshot as snapshot_tools


def _http_status_error(
    status_code: int,
    msg: str = "",
    url: str = "https://test.example.com/admin/api/x",
) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError with a populated response —
    matches the shape that the post-Phase-1a VoogClient raises."""
    req = httpx.Request("GET", url)
    resp = httpx.Response(status_code=status_code, request=req, content=b"")
    return httpx.HTTPStatusError(message=msg or f"HTTP {status_code}", request=req, response=resp)


def _make_client():
    """Build a fake VoogClient with hostname + ecommerce_url already set."""
    client = MagicMock()
    client.host = "test.example.com"
    client.ecommerce_url = "https://test.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_two(self):
        tools = snapshot_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["pages_snapshot", "site_snapshot"])

    def test_pages_snapshot_schema(self):
        tools = {t.name: t for t in snapshot_tools.get_tools()}
        schema = tools["pages_snapshot"].inputSchema
        self.assertEqual(schema["properties"]["output_dir"]["type"], "string")
        self.assertIn("output_dir", schema["required"])

    def test_site_snapshot_schema(self):
        tools = {t.name: t for t in snapshot_tools.get_tools()}
        schema = tools["site_snapshot"].inputSchema
        self.assertEqual(schema["properties"]["output_dir"]["type"], "string")
        self.assertIn("output_dir", schema["required"])

    def test_both_tools_have_full_explicit_annotations(self):
        # Both write to disk (not read-only), additive (not destructive),
        # idempotent (re-running produces same data — both tools write the
        # current Voog state regardless of what existed before).
        tools = snapshot_tools.get_tools()
        for tool in tools:
            ann = tool.annotations
            self.assertIs(
                _ann_get(ann, "readOnlyHint", "read_only_hint"),
                False,
                f"{tool.name} writes to disk → readOnlyHint=False",
            )
            self.assertIs(
                _ann_get(ann, "destructiveHint", "destructive_hint"),
                False,
                f"{tool.name} is additive (not API-destructive)",
            )
            self.assertIs(
                _ann_get(ann, "idempotentHint", "idempotent_hint"),
                True,
                f"{tool.name} is idempotent (same site = same output)",
            )


class TestPagesSnapshot(unittest.TestCase):
    def test_creates_pages_json_and_per_page_contents(self):
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "title": "A", "path": "a"},
            {"id": 2, "title": "B", "path": "b"},
        ]
        client.get.side_effect = [
            [{"id": 11, "name": "title", "value": "Hello A"}],
            [{"id": 22, "name": "title", "value": "Hello B"}],
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "pages_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # Files written
            self.assertTrue((out / "pages.json").exists())
            self.assertTrue((out / "page_1_contents.json").exists())
            self.assertTrue((out / "page_2_contents.json").exists())
            # pages.json contents match
            saved_pages = json.loads((out / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved_pages), 2)
        client.get_all.assert_called_once_with("/pages")
        # 2 calls to /pages/{id}/contents
        self.assertEqual(client.get.call_count, 2)
        # Result has summary + JSON breakdown
        self.assertEqual(len(result), 2)
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["pages"], 2)
        self.assertEqual(breakdown["page_contents_written"], 2)

    def test_per_page_contents_failure_continues(self):
        # If a single page's contents endpoint 404s, the snapshot continues
        # — partial backup is more useful than no backup
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "title": "A"},
            {"id": 2, "title": "B"},
            {"id": 3, "title": "C"},
        ]

        def dispatch(path):
            # Path-based dispatch — parallel_map may invoke in any order,
            # so the exception must be tied to page 2 specifically, not the
            # second positional call.
            if path == "/pages/2/contents":
                raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
            if path == "/pages/1/contents":
                return [{"id": 11}]
            if path == "/pages/3/contents":
                return [{"id": 33}]
            raise AssertionError(f"unexpected path: {path}")

        client.get.side_effect = dispatch
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "pages_snapshot",
                {"output_dir": str(out)},
                client,
            )
            self.assertTrue((out / "page_1_contents.json").exists())
            self.assertFalse((out / "page_2_contents.json").exists())
            self.assertTrue((out / "page_3_contents.json").exists())
        breakdown = json.loads(result[1].text)
        self.assertEqual(breakdown["pages"], 3)
        self.assertEqual(breakdown["page_contents_written"], 2)
        self.assertEqual(len(breakdown["per_page_errors"]), 1)
        self.assertEqual(breakdown["per_page_errors"][0]["page_id"], 2)

    def test_pages_snapshot_uses_parallel_map(self):
        # Lock the contract: per-page contents fan-out goes through
        # voog._concurrency.parallel_map, with the right page ids, the right
        # max_workers, and a fetch fn that hits /pages/{pid}/contents.
        client = _make_client()
        client.get_all.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

        with patch("voog.mcp.tools.snapshot.parallel_map") as mock_pmap:
            mock_pmap.return_value = []
            with tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "snap"
                snapshot_tools.call_tool(
                    "pages_snapshot",
                    {"output_dir": str(out)},
                    client,
                )
            mock_pmap.assert_called_once()
            call_args = mock_pmap.call_args
            self.assertEqual(list(call_args.args[1]), [1, 2, 3])
            self.assertEqual(call_args.kwargs.get("max_workers"), 8)
            # Invoke the captured fn with a fake pid — confirms the lambda
            # actually targets /pages/{pid}/contents, not some other endpoint.
            fetch_fn = call_args.args[0]
            client.get.reset_mock()
            fetch_fn(42)
            client.get.assert_called_once_with("/pages/42/contents")

    def test_creates_parent_dirs(self):
        client = _make_client()
        client.get_all.return_value = []
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "deep" / "nested" / "snap"
            snapshot_tools.call_tool(
                "pages_snapshot",
                {"output_dir": str(out)},
                client,
            )
            self.assertTrue((out / "pages.json").exists())

    def test_existing_dir_overwrites_pages_json(self):
        # pages_snapshot is allowed to overwrite (no atomic refuse).
        # site_snapshot has the stricter refuse-existing semantics.
        client = _make_client()
        client.get_all.return_value = [{"id": 1, "title": "Refresh"}]
        client.get.return_value = []
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            out.mkdir(parents=True)
            (out / "pages.json").write_text("STALE", encoding="utf-8")
            snapshot_tools.call_tool(
                "pages_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # Stale content replaced
            self.assertNotEqual(
                (out / "pages.json").read_text(encoding="utf-8"),
                "STALE",
            )

    def test_pages_endpoint_failure_returns_error(self):
        client = _make_client()
        client.get_all.side_effect = urllib.error.URLError("network down")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "pages_snapshot",
                {"output_dir": str(out)},
                client,
            )
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn("pages_snapshot", payload["error"])

    def test_empty_output_dir_rejected(self):
        client = _make_client()
        result = snapshot_tools.call_tool(
            "pages_snapshot",
            {"output_dir": ""},
            client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_relative_path_rejected(self):
        # Schema description promises absolute path; runtime enforces it so the
        # tool can't silently dump files relative to whatever CWD the MCP
        # server happened to start from
        client = _make_client()
        result = snapshot_tools.call_tool(
            "pages_snapshot",
            {"output_dir": "snapshots/foo"},
            client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("absolute path", payload["error"])

    def test_dot_relative_path_rejected(self):
        client = _make_client()
        result = snapshot_tools.call_tool(
            "pages_snapshot",
            {"output_dir": "./out"},
            client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute path", payload["error"])


class TestSiteSnapshot(unittest.TestCase):
    def test_relative_path_rejected(self):
        client = _make_client()
        result = snapshot_tools.call_tool(
            "site_snapshot",
            {"output_dir": "backups/2026"},
            client,
        )
        client.get_all.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute path", payload["error"])

    def test_refuses_existing_directory(self):
        # site_snapshot's stricter contract: refuse if output_dir exists.
        # Caller must explicitly choose a fresh location to prevent
        # mixing partial old state with new state.
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            out.mkdir(parents=True)  # already exists
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            client.get_all.assert_not_called()
            client.get.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn("exists", payload["error"])

    def test_overwrite_allows_existing_directory(self):
        # I15: overwrite=true bypasses the refuse-existing check for
        # automation/cron use cases. The handler proceeds with the
        # snapshot, files are written into the existing dir.
        client = _make_client()
        client.get_all.return_value = []
        client.get.side_effect = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            out.mkdir(parents=True)  # already exists
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out), "overwrite": True},
                client,
            )
            # Should NOT error on the dir-exists path; should proceed
            # to attempt the API fetches.
            self.assertFalse(getattr(result, "isError", False))
            client.get_all.assert_called()  # at least one list endpoint hit

    def test_overwrite_default_false_in_schema(self):
        # Drift guard: schema "default": False must match handler fallback
        # ``arguments.get("overwrite")`` (None → falsy → refuse). Mirrors the
        # `tests/test_schema_defaults.py` pattern for destructive force gates,
        # but kept local since site_snapshot.overwrite is non-destructive.
        tools = {t.name: t for t in snapshot_tools.get_tools()}
        overwrite_schema = tools["site_snapshot"].inputSchema["properties"]["overwrite"]
        self.assertIs(overwrite_schema["default"], False)

    def test_legacy_force_arg_returns_explicit_error(self):
        # Belt + suspenders for the v1.2.x → v1.3 rename. Schema has
        # ``additionalProperties: false``, but not every MCP client
        # enforces it. If a legacy caller passes ``force=true`` against a
        # lenient client, the handler-level reject ensures they get a
        # clear migration error instead of silent ignore (overwrite would
        # default to false, snapshot would refuse the dir, and the user
        # would wonder why their force=true did nothing).
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out), "force": True},
                client,
            )
            client.get_all.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("force", payload["error"])
            self.assertIn("overwrite", payload["error"])
            self.assertIn("v1.3", payload["error"])

    def test_writes_list_endpoints_singletons_and_per_page_contents(self):
        client = _make_client()
        # get_all called for each list endpoint and /products on ecommerce base
        # get called for each singleton (/site, /me) and per-page/article/product detail
        # Track responses by URL prefix
        list_responses = {
            "/pages": [{"id": 1, "title": "A"}],
            "/articles": [{"id": 100, "title": "Post"}],
        }
        singleton_responses = {
            "/site": {"name": "MySite"},
            "/me": {"id": 1, "email": "test@example.com"},
        }
        per_id_responses = {
            "/pages/1/contents": [{"id": 11, "name": "title"}],
            "/articles/100": {"id": 100, "title": "Post", "body": "hello"},
        }

        def _get_all(path, **kwargs):
            # Products fetched via ecommerce base
            if path == "/products":
                return [{"id": 500, "name": "Widget"}]
            return list_responses.get(path, [])

        def _get(path, **kwargs):
            if path in singleton_responses:
                return singleton_responses[path]
            if path in per_id_responses:
                return per_id_responses[path]
            if path == "/products/500":
                return {"id": 500, "name": "Widget", "translations": {}}
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # List endpoint files
            self.assertTrue((out / "pages.json").exists())
            self.assertTrue((out / "articles.json").exists())
            # Singleton files
            self.assertTrue((out / "site.json").exists())
            self.assertTrue((out / "me.json").exists())
            # Per-page contents
            self.assertTrue((out / "page_1_contents.json").exists())
            # Per-article details
            self.assertTrue((out / "article_100.json").exists())
            # Products + per-product
            self.assertTrue((out / "products.json").exists())
            self.assertTrue((out / "product_500.json").exists())

        # Result has summary + breakdown
        breakdown = json.loads(result[1].text)
        self.assertGreaterEqual(breakdown["files_written"], 7)
        self.assertEqual(breakdown["pages_count"], 1)
        self.assertEqual(breakdown["articles_count"], 1)
        self.assertEqual(breakdown["products_count"], 1)

    def test_site_snapshot_layouts_uses_include_body(self):
        # S1 — site_snapshot must request include_body=true on /layouts.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/layouts":
                # Capture and assert the params kwarg here — easier than
                # introspecting parallel_map call args.
                self.assertEqual(kwargs.get("params"), {"include_body": "true"})
                return [{"id": 1, "title": "default", "body": "<html>...</html>"}]
            if path == "/products":
                return []
            return []

        client.get_all.side_effect = _get_all
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # layouts.json written from the include_body call.
            self.assertTrue((out / "layouts.json").exists())
            layouts_data = json.loads((out / "layouts.json").read_text(encoding="utf-8"))
            self.assertEqual(layouts_data[0]["body"], "<html>...</html>")

    def test_site_snapshot_products_list_includes_variants_translations(self):
        # S2 — products list call must request the full include set.
        client = _make_client()
        captured_params = {}

        def _get_all(path, **kwargs):
            if path == "/products":
                captured_params.update(kwargs.get("params") or {})
                return [
                    {
                        "id": 500,
                        "name": "Widget",
                        "translations": {"en": {}},
                        "variants": [{"id": 1, "stock": 10}],
                        "variant_types": [],
                    },
                ]
            return []

        client.get_all.side_effect = _get_all
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
        # The include must be present on the products list call.
        self.assertEqual(
            captured_params.get("include"),
            "variants,variant_types,translations",
        )

    def test_site_snapshot_does_not_fetch_per_product_detail(self):
        # S2 — the per-product detail fan-out is removed; product_{id}.json
        # files come from the list response directly.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/products":
                return [
                    {"id": 500, "name": "A", "translations": {}, "variants": []},
                    {"id": 501, "name": "B", "translations": {}, "variants": []},
                ]
            return []

        client.get_all.side_effect = _get_all
        # client.get should NEVER be called with /products/{id} after S2.
        get_calls: list = []

        def _get(path, **kwargs):
            get_calls.append(path)
            return {}

        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # Per-product detail files still written, from list data.
            self.assertTrue((out / "product_500.json").exists())
            self.assertTrue((out / "product_501.json").exists())
            detail_500 = json.loads((out / "product_500.json").read_text(encoding="utf-8"))
            self.assertEqual(detail_500["id"], 500)
            self.assertEqual(detail_500["name"], "A")
        # Verify NO /products/{id} fetches happened. Other GETs (singletons,
        # page contents, public HTML) are fine.
        product_detail_calls = [p for p in get_calls if p.startswith("/products/")]
        self.assertEqual(product_detail_calls, [])

    def test_site_snapshot_products_handles_stripped_list_response(self):
        # S2 (PR #123 pass-4 review): pin current behaviour when Voog returns
        # a stripped products list (no variants/translations). Today we write
        # the list response as-is into both products.json and per-product
        # files — no fallback to per-id detail fan-out. If a future change
        # adds the fallback (mirroring S1's "trust the field, fall back when
        # missing" pattern), this test will fail loudly so the design choice
        # is deliberate.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/products":
                # Voog silently ignored the include — stripped items returned.
                return [{"id": 500, "name": "Widget"}]
            return []

        client.get_all.side_effect = _get_all
        get_calls: list = []

        def _get(path, **kwargs):
            get_calls.append(path)
            return {}

        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # File written, content is the stripped shape.
            self.assertTrue((out / "product_500.json").exists())
            detail = json.loads((out / "product_500.json").read_text(encoding="utf-8"))
            self.assertEqual(detail, {"id": 500, "name": "Widget"})
            self.assertNotIn("variants", detail)
            self.assertNotIn("translations", detail)
        # Today: NO per-id detail fetch attempted to recover the missing
        # fields. If this test fails because /products/500 appears in
        # get_calls, the design has changed — update the docstring at
        # snapshot.py:_site_snapshot's S2 block to reflect the new behaviour.
        product_detail_calls = [p for p in get_calls if p.startswith("/products/")]
        self.assertEqual(product_detail_calls, [])

    def test_404_endpoints_skipped_not_fatal(self):
        # /elements often 404s on sites that don't use the elements feature.
        # Snapshot must continue, log the skip, but not fail.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/elements":
                raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
            return []  # everything else returns empty list

        def _get(path, **kwargs):
            return {}

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # Other list endpoints still got their files
            self.assertTrue((out / "pages.json").exists())
            self.assertFalse((out / "elements.json").exists())  # skipped
        breakdown = json.loads(result[1].text)
        self.assertGreaterEqual(len(breakdown["skipped"]), 1)
        skipped_files = [s["file"] for s in breakdown["skipped"]]
        self.assertIn("elements.json", skipped_files)

    def test_empty_output_dir_rejected(self):
        client = _make_client()
        result = snapshot_tools.call_tool(
            "site_snapshot",
            {"output_dir": ""},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_parallel_map_invoked_with_correct_args(self):
        # Faas 2 contract: list endpoints + per-page + per-article + per-product
        # detail loops fan out via parallel_map at max_workers=8. Verify the
        # snapshot module dispatches each loop through the helper with the
        # expected items + max_workers.
        client = _make_client()

        list_responses = {
            "/pages": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}],
            "/articles": [{"id": 100, "title": "Post"}],
        }

        def _get_all(path, **kwargs):
            if path == "/products":
                return [{"id": 500, "name": "Widget"}, {"id": 501, "name": "Gadget"}]
            return list_responses.get(path, [])

        def _get(path, **kwargs):
            if path in ("/site", "/me"):
                return {}
            if path == "/pages/1/contents":
                return [{"id": 11}]
            if path == "/pages/2/contents":
                return [{"id": 12}]
            if path == "/articles/100":
                return {"id": 100, "body": "x"}
            if path.startswith("/products/"):
                return {"id": int(path.rsplit("/", 1)[1])}
            return {}

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            with patch(
                "voog.mcp.tools.snapshot.parallel_map",
                wraps=snapshot_tools.parallel_map,
            ) as mock_pm:
                snapshot_tools.call_tool(
                    "site_snapshot",
                    {"output_dir": str(out)},
                    client,
                )

        # Expect 3 parallel_map calls (post-S2): list endpoints, page contents,
        # article details. Per-product detail fan-out removed in v1.4 S2 —
        # /products list with ?include=variants,variant_types,translations
        # carries the full detail shape.
        self.assertEqual(mock_pm.call_count, 3)

        # Every call must be max_workers=8 (read-only fan-out per spec § 4.3).
        for call in mock_pm.call_args_list:
            self.assertEqual(call.kwargs.get("max_workers"), 8)

        items_per_call = [call.args[1] for call in mock_pm.call_args_list]
        # Loop 1: list endpoints — items are ``(endpoint, params_or_None)``
        # tuples per v1.4 design fix (PR #123 pass-2 review).
        self.assertEqual(items_per_call[0], snapshot_tools.SITE_SNAPSHOT_LIST_ENDPOINTS)
        # Pin specific endpoints + params to catch accidental constant edits
        # — the shape-vs-constant equality above updates in lockstep with the
        # constant itself, so these explicit-presence assertions catch the
        # "oh, I'll just drop /layouts" regression (PR #123 pass-3 review).
        # /products is intentionally NOT here — it has its own ecommerce-base
        # fetch outside this loop.
        endpoints_in_loop = [item[0] for item in items_per_call[0]]
        self.assertIn("/layouts", endpoints_in_loop)
        self.assertIn("/pages", endpoints_in_loop)
        self.assertIn("/articles", endpoints_in_loop)
        self.assertNotIn("/products", endpoints_in_loop)
        # /layouts specifically carries include_body=true params.
        layouts_entry = next(item for item in items_per_call[0] if item[0] == "/layouts")
        self.assertEqual(layouts_entry[1], {"include_body": "true"})
        # Loop 2: page IDs
        self.assertEqual(items_per_call[1], [1, 2])
        # Loop 3: article IDs
        self.assertEqual(items_per_call[2], [100])

    def test_partial_failure_does_not_abort_other_resources(self):
        # Spec § 4.5 — failure of one parallel fetch must not abort siblings.
        # Mock one per-page contents call to raise; assert it shows up in
        # `skipped`, the other resources still get written.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/pages":
                return [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}, {"id": 3, "title": "C"}]
            if path == "/products":
                return []
            return []

        def _get(path, **kwargs):
            if path == "/pages/2/contents":
                # Simulate one fetch failing — others succeed.
                raise urllib.error.HTTPError("u", 500, "Server Error", {}, None)
            if path in ("/site", "/me"):
                return {}
            if path.startswith("/pages/") and path.endswith("/contents"):
                return [{"id": 99}]
            return {}

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            # The 2 successful per-page contents files exist; failed one does not.
            self.assertTrue((out / "page_1_contents.json").exists())
            self.assertFalse((out / "page_2_contents.json").exists())
            self.assertTrue((out / "page_3_contents.json").exists())
            # Sibling resources still written despite partial failure.
            self.assertTrue((out / "pages.json").exists())

        breakdown = json.loads(result[1].text)
        # Exactly one skipped entry for the failed page contents.
        skipped_files = [s["file"] for s in breakdown["skipped"]]
        self.assertIn("page_2_contents.json", skipped_files)
        # Counter accurate: 2 of 3 written.
        self.assertEqual(breakdown["page_contents_written"], 2)

    def test_public_html_fetch_uses_timeout(self):
        # Public HTML fetch (rendered samples) is unauthenticated and runs
        # outside VoogClient — it needs its own timeout so a hung host
        # cannot wedge the long-running MCP server.
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/pages":
                return [{"id": 1, "title": "Home", "path": "", "content_type": "default"}]
            return []

        def _get(path, **kwargs):
            if path in ("/site", "/me"):
                return {}
            if path == "/pages/1/contents":
                return []
            return {}

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            with patch("voog.mcp.tools.snapshot.urllib.request.urlopen") as mock_urlopen:
                fake = MagicMock()
                fake.read.return_value = b"<html></html>"
                mock_urlopen.return_value.__enter__.return_value = fake
                snapshot_tools.call_tool(
                    "site_snapshot",
                    {"output_dir": str(out)},
                    client,
                )
            self.assertGreaterEqual(mock_urlopen.call_count, 1)
            # Every public-fetch call must be bounded by an explicit timeout.
            for call in mock_urlopen.call_args_list:
                self.assertIn(
                    "timeout",
                    call.kwargs,
                    "snapshot public HTML fetch missing timeout=",
                )
                self.assertEqual(call.kwargs["timeout"], 30)


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = _make_client()
        result = snapshot_tools.call_tool(
            "nonexistent",
            {},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestSlugifyPath(unittest.TestCase):
    """Direct unit tests for the URL→slug helper."""

    def test_empty_string_becomes_home(self):
        self.assertEqual(snapshot_tools._slugify_path(""), "home")

    def test_root_slash_becomes_home(self):
        self.assertEqual(snapshot_tools._slugify_path("/"), "home")

    def test_none_becomes_home(self):
        self.assertEqual(snapshot_tools._slugify_path(None), "home")

    def test_simple_path(self):
        self.assertEqual(snapshot_tools._slugify_path("blog"), "blog")

    def test_nested_path_dashed(self):
        self.assertEqual(snapshot_tools._slugify_path("blog/2026/post"), "blog-2026-post")

    def test_strips_leading_trailing_slashes(self):
        self.assertEqual(snapshot_tools._slugify_path("/blog/"), "blog")

    def test_uppercase_lowercased(self):
        self.assertEqual(snapshot_tools._slugify_path("Blog/Post"), "blog-post")

    def test_special_chars_replaced(self):
        self.assertEqual(snapshot_tools._slugify_path("blog/my post!"), "blog-my-post")

    def test_only_specials_falls_back_to_home(self):
        # If a path collapses to nothing after slugification, return "home"
        # rather than empty string (filename safety)
        self.assertEqual(snapshot_tools._slugify_path("!!!"), "home")


class TestPickSamplePagePaths(unittest.TestCase):
    """Direct unit tests for the rendered-HTML sample-selection heuristic."""

    def test_empty_pages_returns_empty(self):
        self.assertEqual(snapshot_tools._pick_sample_page_paths([]), [])

    def test_prefers_front_page_first(self):
        # Front page (empty path) must be picked first regardless of input order
        pages = [
            {"path": "blog/post", "content_type": "page"},
            {"path": "", "content_type": "page"},
        ]
        result = snapshot_tools._pick_sample_page_paths(pages, max_samples=1)
        self.assertEqual(result, ["/"])

    def test_picks_one_per_content_type(self):
        # Variety wins over duplicates: 3 pages with 3 different content_types
        pages = [
            {"path": "", "content_type": "page"},
            {"path": "blog", "content_type": "blog"},
            {"path": "shop", "content_type": "shop"},
        ]
        result = snapshot_tools._pick_sample_page_paths(pages, max_samples=3)
        self.assertEqual(set(result), {"/", "/blog", "/shop"})

    def test_skips_hidden_pages(self):
        pages = [
            {"path": "", "content_type": "page", "hidden": False},
            {"path": "secret", "content_type": "page", "hidden": True},
        ]
        result = snapshot_tools._pick_sample_page_paths(pages, max_samples=2)
        self.assertEqual(result, ["/"])

    def test_falls_back_to_hidden_when_all_hidden(self):
        # Edge case: site with ONLY hidden pages still picks samples (better
        # than zero coverage in a snapshot)
        pages = [
            {"path": "wip", "content_type": "page", "hidden": True},
        ]
        result = snapshot_tools._pick_sample_page_paths(pages, max_samples=1)
        self.assertEqual(result, ["/wip"])

    def test_max_samples_caps_output(self):
        pages = [{"path": str(i), "content_type": f"ct{i}"} for i in range(10)]
        result = snapshot_tools._pick_sample_page_paths(pages, max_samples=3)
        self.assertEqual(len(result), 3)

    def test_content_type_iteration_is_deterministic(self):
        # Two distinct content_types ranked by sorted(ct) — output must be
        # stable across input orderings so two snapshot runs against the same
        # site produce identical sample lists.
        pages_a = [
            {"path": "", "content_type": "page"},
            {"path": "blog/x", "content_type": "blog"},
            {"path": "shop/y", "content_type": "shop"},
        ]
        pages_b = list(reversed(pages_a))
        result_a = snapshot_tools._pick_sample_page_paths(pages_a, max_samples=2)
        result_b = snapshot_tools._pick_sample_page_paths(pages_b, max_samples=2)
        # Front page first, then "blog" (sorted before "shop")
        self.assertEqual(result_a, ["/", "/blog/x"])
        self.assertEqual(result_b, ["/", "/blog/x"])


class TestManifestSchema(unittest.TestCase):
    """Manifest dataclass schema — what restore tooling will consume."""

    def test_to_dict_has_all_required_keys_when_request_count_absent(self):
        # request_count is omitted until Phase 6's counter lands — until
        # then operators reading _meta.json should NOT see a misleading
        # "0" value that looks like a counting bug.
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="stella",
            host="stellasoomlais.com",
            created_at="2026-05-26T13:42:18Z",
        )
        d = m.to_dict()
        expected_keys = {
            "voog_mcp_version",
            "created_at",
            "site",
            "host",
            "attempted",
            "succeeded",
            "skipped",
            "failed",
            "duration_seconds",
            "aborted_reason",
            "partial",
        }
        self.assertEqual(set(d.keys()), expected_keys)
        self.assertNotIn("request_count", d)

    def test_to_dict_emits_request_count_when_set(self):
        # Forward-compat: once Phase 6's counter lands and populates
        # manifest.request_count, the field appears.
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="stella",
            host="stellasoomlais.com",
            created_at="2026-05-26T13:42:18Z",
            request_count=42,
        )
        d = m.to_dict()
        self.assertEqual(d["request_count"], 42)

    def test_partial_false_when_all_succeed(self):
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            attempted=["/pages", "/articles"],
            succeeded=["/pages", "/articles"],
        )
        self.assertFalse(m.to_dict()["partial"])

    def test_partial_true_when_skipped_nonempty(self):
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            attempted=["/pages", "/elements"],
            succeeded=["/pages"],
            skipped=[{"endpoint": "/elements", "reason": "404"}],
        )
        self.assertTrue(m.to_dict()["partial"])

    def test_partial_true_when_failed_nonempty(self):
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            attempted=["/pages"],
            succeeded=[],
            failed=[{"endpoint": "/pages", "reason": "HTTP 500"}],
        )
        self.assertTrue(m.to_dict()["partial"])

    def test_partial_true_when_aborted_reason_set(self):
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            attempted=["/pages"],
            succeeded=["/pages"],
            aborted_reason="request_budget_exceeded",
        )
        self.assertTrue(m.to_dict()["partial"])

    def test_partial_true_when_attempted_differs_from_succeeded(self):
        # Edge case: a fetch dispatched (in attempted) but never categorised
        # (not in succeeded/skipped/failed). Should still surface as partial,
        # since the caller is missing data they expected.
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            attempted=["/pages", "/articles"],
            succeeded=["/pages"],
        )
        self.assertTrue(m.to_dict()["partial"])

    def test_duration_seconds_rounded_to_3_decimals(self):
        from voog.mcp.tools.snapshot import _Manifest

        m = _Manifest(
            voog_mcp_version="1.4",
            site="x",
            host="x.com",
            created_at="2026-05-26T00:00:00Z",
            duration_seconds=18.4156789,
        )
        self.assertEqual(m.to_dict()["duration_seconds"], 18.416)


class TestManifestEmission(unittest.TestCase):
    """MD4 / S7 — ``_meta.json`` is written on every exit path."""

    def test_manifest_written_on_full_success(self):
        client = _make_client()
        client.get_all.return_value = []  # all list endpoints empty
        client.get.return_value = {}  # all singletons OK, no per-id fanout

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta_path = out / "_meta.json"
            self.assertTrue(meta_path.exists())
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["voog_mcp_version"], snapshot_tools._voog_version)
            self.assertEqual(meta["host"], "test.example.com")
            self.assertIn("/pages", meta["attempted"])
            self.assertIn("/site", meta["attempted"])
            self.assertIn("/products", meta["attempted"])
            # All list endpoints succeeded (empty arrays are still successful)
            self.assertIn("/pages", meta["succeeded"])
            self.assertEqual(meta["failed"], [])
            self.assertIsNone(meta["aborted_reason"])
            self.assertGreaterEqual(meta["duration_seconds"], 0.0)

        breakdown = json.loads(result[1].text)
        self.assertIn("manifest_path", breakdown)
        self.assertIn("partial", breakdown)
        # Empty data + all-succeed branch → partial=False
        self.assertFalse(breakdown["partial"])

    def test_manifest_records_404_as_skipped(self):
        # 4xx → skipped (endpoint not available on this tenant).
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/elements":
                raise _http_status_error(404, "Not Found")
            return []

        client.get_all.side_effect = _get_all
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            skipped_endpoints = [s["endpoint"] for s in meta["skipped"]]
            failed_endpoints = [s["endpoint"] for s in meta["failed"]]
            self.assertIn("/elements", skipped_endpoints)
            self.assertNotIn("/elements", failed_endpoints)
            self.assertTrue(meta["partial"])

    def test_manifest_records_403_singleton_as_skipped(self):
        # Mirrors the real-world Stella OLD baseline: /me returns 403
        # because the API key lacks the user-info scope. Must land in
        # skipped[], not failed[].
        client = _make_client()
        client.get_all.return_value = []

        def _get(path, **kwargs):
            if path == "/me":
                raise _http_status_error(403, "Forbidden")
            return {}

        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            skipped_endpoints = [s["endpoint"] for s in meta["skipped"]]
            self.assertIn("/me", skipped_endpoints)
            # /site should still succeed
            self.assertIn("/site", meta["succeeded"])

    def test_manifest_records_500_as_failed(self):
        # 5xx → failed (the fetch should have worked).
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/pages":
                raise _http_status_error(500, "Server Error")
            return []

        client.get_all.side_effect = _get_all
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            failed_endpoints = [s["endpoint"] for s in meta["failed"]]
            self.assertIn("/pages", failed_endpoints)

        breakdown = json.loads(result[1].text)
        self.assertTrue(breakdown["partial"])

    def test_manifest_written_on_mid_snapshot_abort_request_budget(self):
        # Simulate Phase 6 RequestBudgetExceeded raising mid-fetch.
        # Phase 5 catches by class name (forward-compat); Phase 6 tightens.
        class RequestBudgetExceeded(Exception):
            pass

        client = _make_client()

        # All list endpoints raise the budget exception — the parallel_map
        # surfaces it as the first failed item; categorisation enters the
        # except branch when we touch the singletons.
        def _get_all(path, **kwargs):
            raise RequestBudgetExceeded("budget hit")

        def _get(path, **kwargs):
            # Singleton fetch is the first sequential call after the
            # parallel batch returns; raising here trips the outer except.
            raise RequestBudgetExceeded("budget hit on singleton")

        client.get_all.side_effect = _get_all
        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            with self.assertRaises(RequestBudgetExceeded):
                snapshot_tools.call_tool(
                    "site_snapshot",
                    {"output_dir": str(out)},
                    client,
                )
            # MD4: manifest written despite re-raise
            meta_path = out / "_meta.json"
            self.assertTrue(meta_path.exists())
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["aborted_reason"], "request_budget_exceeded")
            self.assertTrue(meta["partial"])

    def test_manifest_written_on_daily_quota_abort(self):
        class DailyQuotaExceeded(Exception):
            pass

        client = _make_client()
        client.get_all.return_value = []
        client.get.side_effect = DailyQuotaExceeded("quota exhausted")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            with self.assertRaises(DailyQuotaExceeded):
                snapshot_tools.call_tool(
                    "site_snapshot",
                    {"output_dir": str(out)},
                    client,
                )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["aborted_reason"], "daily_quota_exceeded")
            self.assertTrue(meta["partial"])

    def test_non_budget_exception_does_not_abort(self):
        # Mirror of the abort test: a RuntimeError inside a step's
        # try/except is categorised as ``failed`` for that endpoint
        # (5xx-like), NOT propagated as an abort. The unexpected-class
        # ``aborted_reason`` branch is defense-in-depth for anything
        # that escapes a per-step try/except — not reachable through the
        # public API today but kept to document the intent if a future
        # refactor adds a code path outside the per-step guards.
        client = _make_client()
        client.get_all.return_value = []
        client.get.side_effect = RuntimeError("transient hiccup")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            # Does NOT raise — each singleton call is caught and recorded
            # as failed.
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            # Both singletons land in failed[] (RuntimeError → "failed")
            failed_endpoints = [s["endpoint"] for s in meta["failed"]]
            self.assertIn("/site", failed_endpoints)
            self.assertIn("/me", failed_endpoints)
            # No abort — finished normally, just with errors recorded
            self.assertIsNone(meta["aborted_reason"])
            self.assertTrue(meta["partial"])

    def test_partial_flag_in_summary_when_skipped(self):
        client = _make_client()

        def _get_all(path, **kwargs):
            if path == "/elements":
                raise _http_status_error(404, "Not Found")
            return []

        client.get_all.side_effect = _get_all
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            result = snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
        breakdown = json.loads(result[1].text)
        self.assertTrue(breakdown["partial"])
        # Summary string contains marker
        self.assertIn("[PARTIAL]", result[0].text)

    def test_request_count_read_from_client_attribute(self):
        # Forward-compat: when Phase 6 lands ``_request_count`` on the
        # client, manifest picks it up. Phase 5 omits the field entirely
        # when the counter doesn't exist (avoids misleading zero).
        client = _make_client()
        client.get_all.return_value = []
        client.get.return_value = {}
        # Simulate Phase 6 already-merged scenario:
        client._request_count = 42

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["request_count"], 42)

    def test_request_count_absent_when_counter_not_yet_landed(self):
        # Phase 5 reality: VoogClient has no ``_request_count`` yet.
        # The manifest omits the field entirely — operators reading
        # _meta.json don't see "0" and assume a counting bug.
        client = _make_client()
        # MagicMock auto-creates ``_request_count`` as a MagicMock attr,
        # so we explicitly delete it to simulate the no-counter scenario:
        del client._request_count
        client.get_all.return_value = []
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "snap"
            snapshot_tools.call_tool(
                "site_snapshot",
                {"output_dir": str(out)},
                client,
            )
            meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
            self.assertNotIn("request_count", meta)


class TestClassifyApiExc(unittest.TestCase):
    """Direct unit tests for ``_classify_api_exc`` — 4xx → skipped,
    5xx + everything else → failed. Mirrors the empirical Stella OLD
    captures where 403 ``/me`` lands in skipped, not failed."""

    def test_404_httpx_is_skipped(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(404)), "skipped")

    def test_403_httpx_is_skipped(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(403)), "skipped")

    def test_500_httpx_is_failed(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(500)), "failed")

    def test_502_httpx_is_failed(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(502)), "failed")

    def test_429_httpx_is_failed_not_skipped(self):
        # 429 reaching the snapshot classifier means _request's retry
        # loop already exhausted (429 IS in _RETRYABLE_STATUS). That's a
        # real failure ("should have worked, server pushed back"), not
        # "endpoint not available to this caller". Restore tooling
        # tolerating 429 as ``skipped`` would be wrong.
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(429)), "failed")

    def test_408_httpx_is_failed_not_skipped(self):
        # 408 Request Timeout — server gave up waiting; the request
        # itself was fine. Same "should have worked" bucket as 429 + 5xx.
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(_http_status_error(408)), "failed")

    def test_429_urllib_is_failed_not_skipped(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        exc = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        self.assertEqual(_classify_api_exc(exc), "failed")

    def test_urllib_404_is_skipped(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        exc = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        self.assertEqual(_classify_api_exc(exc), "skipped")

    def test_generic_exception_is_failed(self):
        from voog.mcp.tools.snapshot import _classify_api_exc

        self.assertEqual(_classify_api_exc(RuntimeError("oops")), "failed")


class TestIsAbortException(unittest.TestCase):
    """MD4 — name-based detection of Phase 6 budget/quota exceptions."""

    def test_request_budget_exceeded_by_name(self):
        from voog.mcp.tools.snapshot import _is_abort_exception

        class RequestBudgetExceeded(Exception):
            pass

        self.assertTrue(_is_abort_exception(RequestBudgetExceeded()))

    def test_daily_quota_exceeded_by_name(self):
        from voog.mcp.tools.snapshot import _is_abort_exception

        class DailyQuotaExceeded(Exception):
            pass

        self.assertTrue(_is_abort_exception(DailyQuotaExceeded()))

    def test_random_exception_is_not_abort(self):
        from voog.mcp.tools.snapshot import _is_abort_exception

        self.assertFalse(_is_abort_exception(RuntimeError("hiccup")))
        self.assertFalse(_is_abort_exception(_http_status_error(500)))


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — snapshot_tools joined to TOOL_GROUPS."""

    def test_snapshot_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(snapshot_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        from voog.mcp import server

        all_names = [tool.name for group in server.TOOL_GROUPS for tool in group.get_tools()]
        self.assertEqual(len(all_names), len(set(all_names)), f"Duplicate tool names: {all_names}")

    def test_phase_c_complete(self):
        # Sentinel: after Task 11b + product_set_images, TOOL_GROUPS should
        # cover all 6 spec § 4 groups (pages, pages_mutate, layouts, snapshot,
        # products, redirects) plus layouts_sync (Task 11b — filesystem-
        # touching layouts pull/push) and products_images (deferred from
        # Task 13 — 3-step asset upload protocol).
        # Task 2 (endpoint coverage): raw passthrough tools also added.
        # Task 10: ecommerce_settings and site singleton tools added.
        from voog.mcp import server
        from voog.mcp.tools import (
            articles as articles_t,
        )
        from voog.mcp.tools import (
            cart_rules as cart_rules_t,
        )
        from voog.mcp.tools import (
            categories as categories_t,
        )
        from voog.mcp.tools import (
            comments as comments_t,
        )
        from voog.mcp.tools import (
            content_partials as content_partials_t,
        )
        from voog.mcp.tools import (
            discounts as discounts_t,
        )
        from voog.mcp.tools import (
            ecommerce_settings as ecommerce_settings_t,
        )
        from voog.mcp.tools import (
            elements as elements_t,
        )
        from voog.mcp.tools import (
            layouts as layouts_t,
        )
        from voog.mcp.tools import (
            layouts_sync as layouts_sync_t,
        )
        from voog.mcp.tools import (
            me as me_t,
        )
        from voog.mcp.tools import (
            multilingual as multilingual_t,
        )
        from voog.mcp.tools import (
            orders as orders_t,
        )
        from voog.mcp.tools import (
            pages as pages_t,
        )
        from voog.mcp.tools import (
            pages_mutate as pages_mutate_t,
        )
        from voog.mcp.tools import (
            products as products_t,
        )
        from voog.mcp.tools import (
            products_images as products_images_t,
        )
        from voog.mcp.tools import (
            raw as raw_t,
        )
        from voog.mcp.tools import (
            redirects as redirects_t,
        )
        from voog.mcp.tools import (
            search as search_t,
        )
        from voog.mcp.tools import (
            shipping as shipping_t,
        )
        from voog.mcp.tools import (
            site as site_t,
        )
        from voog.mcp.tools import (
            snapshot as snapshot_t,
        )
        from voog.mcp.tools import (
            tags as tags_t,
        )
        from voog.mcp.tools import (
            texts as texts_t,
        )
        from voog.mcp.tools import (
            webhooks as webhooks_t,
        )

        expected = {
            articles_t,
            cart_rules_t,
            categories_t,
            comments_t,
            content_partials_t,
            discounts_t,
            ecommerce_settings_t,
            elements_t,
            layouts_t,
            layouts_sync_t,
            me_t,
            multilingual_t,
            orders_t,
            pages_t,
            pages_mutate_t,
            products_t,
            products_images_t,
            raw_t,
            redirects_t,
            search_t,
            shipping_t,
            site_t,
            snapshot_t,
            tags_t,
            texts_t,
            webhooks_t,
        }
        self.assertEqual(set(server.TOOL_GROUPS), expected)


class TestAllToolsRequireSite(unittest.TestCase):
    # voog_list_my_sites is intentional exception (uses token+host, not site config).
    _NO_SITE_ALLOWLIST = frozenset({"voog_list_my_sites"})

    def test_all_tools_require_site(self):
        from voog.mcp.tools import snapshot as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )

    def test_no_site_allowlist_drift(self):
        # Defense in depth: enumerate ALL tools across ALL groups, and
        # assert that any tool missing `site` from required is in the
        # allowlist. Catches regressions if a new tool forgets to
        # require `site` and isn't a legitimate exception.
        from voog.mcp import server

        offenders: list[str] = []
        for group in server.TOOL_GROUPS:
            for tool in group.get_tools():
                if "site" not in tool.inputSchema.get("required", []):
                    if tool.name not in self._NO_SITE_ALLOWLIST:
                        offenders.append(tool.name)
        self.assertEqual(
            offenders,
            [],
            f"Tools missing 'site' from required and not in allowlist: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
