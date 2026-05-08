"""Tests for voog.mcp.tools.snapshot."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._test_helpers import _ann_get
from voog.mcp.tools import snapshot as snapshot_tools


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

        # Expect 4 parallel_map calls: list endpoints, page contents, article
        # details, product details. Singletons + rendered HTML stay sequential.
        self.assertEqual(mock_pm.call_count, 4)

        # Every call must be max_workers=8 (read-only fan-out per spec § 4.3).
        for call in mock_pm.call_args_list:
            self.assertEqual(call.kwargs.get("max_workers"), 8)

        # Verify the 4 calls received the expected item lists.
        items_per_call = [call.args[1] for call in mock_pm.call_args_list]

        # Loop 1: list endpoints
        self.assertEqual(items_per_call[0], snapshot_tools.SITE_SNAPSHOT_LIST_ENDPOINTS)
        # Loop 3: page IDs
        self.assertEqual(items_per_call[1], [1, 2])
        # Loop 4: article IDs
        self.assertEqual(items_per_call[2], [100])
        # Loop 5: product IDs
        self.assertEqual(items_per_call[3], [500, 501])

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
            content_partials as content_partials_t,
        )
        from voog.mcp.tools import (
            ecommerce_settings as ecommerce_settings_t,
        )
        from voog.mcp.tools import (
            layouts as layouts_t,
        )
        from voog.mcp.tools import (
            layouts_sync as layouts_sync_t,
        )
        from voog.mcp.tools import (
            multilingual as multilingual_t,
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
            site as site_t,
        )
        from voog.mcp.tools import (
            snapshot as snapshot_t,
        )
        from voog.mcp.tools import (
            texts as texts_t,
        )
        from voog.mcp.tools import (
            webhooks as webhooks_t,
        )

        expected = {
            articles_t,
            content_partials_t,
            ecommerce_settings_t,
            layouts_t,
            layouts_sync_t,
            multilingual_t,
            pages_t,
            pages_mutate_t,
            products_t,
            products_images_t,
            raw_t,
            redirects_t,
            site_t,
            snapshot_t,
            texts_t,
            webhooks_t,
        }
        self.assertEqual(set(server.TOOL_GROUPS), expected)


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import snapshot as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


if __name__ == "__main__":
    unittest.main()
