"""Tests for voog.cli.commands.snapshot — site-snapshot + pages-snapshot.

site-snapshot is a comprehensive read-only backup; tests focus on the
output-directory contract (must not exist, gets created), the per-list
"continue on error" behavior, and the ecommerce vs admin URL split.
pages-snapshot is a smaller subset; tests cover the per-page errors
and the non-zero exit code when any page failed.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import snapshot as snap_cmd


def _make_client():
    client = MagicMock()
    client.host = "example.com"
    client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
    return client


class TestSiteSnapshotOutputDir(unittest.TestCase):
    def test_existing_output_dir_is_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            out.mkdir()
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                rc = snap_cmd.cmd_site_snapshot(args, client)
            self.assertEqual(rc, 1)
            # Command exits before any API calls
            client.get_all.assert_not_called()
            self.assertIn("already exists", stderr.getvalue())

    def test_output_dir_created_when_missing(self):
        client = _make_client()
        # Make all list endpoints empty to keep the test fast.
        client.get_all.return_value = []
        client.get.return_value = {}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                with patch("urllib.request.urlopen"):
                    rc = snap_cmd.cmd_site_snapshot(args, client)
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            self.assertTrue(out.is_dir())


class TestSiteSnapshotEndpoints(unittest.TestCase):
    def test_writes_per_list_endpoint_files(self):
        client = _make_client()

        def get_all_dispatch(endpoint, **kwargs):
            return {
                "/pages": [{"id": 1, "path": "p1"}],
                "/articles": [{"id": 2}],
                "/products": [{"id": 3, "name": "P"}],
            }.get(endpoint, [])

        client.get_all.side_effect = get_all_dispatch
        client.get.side_effect = lambda path, **kw: {}  # singletons + per-id details

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            # urlopen returns a context manager whose .read() yields bytes;
            # the rendered-HTML step will try to decode + write to disk.
            fake_resp = MagicMock()
            fake_resp.read.return_value = b"<html></html>"
            urlopen_cm = MagicMock()
            urlopen_cm.__enter__.return_value = fake_resp
            with patch("sys.stdout"):
                with patch("urllib.request.urlopen", return_value=urlopen_cm):
                    rc = snap_cmd.cmd_site_snapshot(args, client)
            self.assertEqual(rc, 0)
            # Pages and articles list files written from the standard list endpoints
            files = {p.name for p in out.iterdir()}
            self.assertIn("pages.json", files)
            self.assertIn("articles.json", files)
            # Ecommerce products use a different URL base — verify the call shape
            ecommerce_calls = [
                c
                for c in client.get_all.call_args_list
                if c.kwargs.get("base") == client.ecommerce_url
            ]
            self.assertTrue(any("/products" in c.args for c in ecommerce_calls))

    def test_cli_site_snapshot_writes_layouts_with_include_body(self):
        # S1 (v1.4): CLI must mirror the MCP tool — separate /layouts call
        # with include_body=true so layouts.json is written (was silently
        # dropped when /layouts was pulled out of SITE_SNAPSHOT_LIST_ENDPOINTS).
        client = _make_client()
        captured_layouts_params = {}

        def get_all_dispatch(endpoint, **kwargs):
            if endpoint == "/layouts":
                captured_layouts_params.update(kwargs.get("params") or {})
                return [{"id": 1, "title": "default", "body": "<html>...</html>"}]
            return []

        client.get_all.side_effect = get_all_dispatch
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                with patch("urllib.request.urlopen"):
                    rc = snap_cmd.cmd_site_snapshot(args, client)
            self.assertEqual(rc, 0)
            self.assertTrue((out / "layouts.json").exists())
            layouts_data = json.loads((out / "layouts.json").read_text(encoding="utf-8"))
            self.assertEqual(layouts_data[0]["body"], "<html>...</html>")
        self.assertEqual(captured_layouts_params, {"include_body": "true"})

    def test_cli_site_snapshot_products_uses_include_and_skips_detail_fanout(self):
        # S2 (v1.4): CLI mirrors MCP tool — products list with
        # ?include=variants,variant_types,translations, no per-product GET.
        client = _make_client()
        captured_products_params = {}

        def get_all_dispatch(endpoint, **kwargs):
            if endpoint == "/products":
                captured_products_params.update(kwargs.get("params") or {})
                return [
                    {"id": 500, "name": "A", "translations": {}, "variants": []},
                    {"id": 501, "name": "B", "translations": {}, "variants": []},
                ]
            return []

        client.get_all.side_effect = get_all_dispatch
        get_calls: list = []

        def _get(path, **kwargs):
            get_calls.append(path)
            return {}

        client.get.side_effect = _get

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                with patch("urllib.request.urlopen"):
                    rc = snap_cmd.cmd_site_snapshot(args, client)
            self.assertEqual(rc, 0)
            # Per-product files still written, from list data.
            self.assertTrue((out / "product_500.json").exists())
            self.assertTrue((out / "product_501.json").exists())
            detail = json.loads((out / "product_500.json").read_text(encoding="utf-8"))
            self.assertEqual(detail["id"], 500)
        # Include set on the products list call.
        self.assertEqual(
            captured_products_params.get("include"),
            "variants,variant_types,translations",
        )
        # No /products/{id} fetches — S2 drops the per-id fan-out entirely.
        product_detail_calls = [p for p in get_calls if p.startswith("/products/")]
        self.assertEqual(product_detail_calls, [])

    def test_failed_list_endpoint_does_not_abort(self):
        client = _make_client()

        # First endpoint fails, the rest return empty. Snapshot must continue
        # rather than abort — partial output is more useful than nothing.
        def get_all_dispatch(endpoint, **kwargs):
            if endpoint == "/pages":
                raise RuntimeError("API timeout")
            return []

        client.get_all.side_effect = get_all_dispatch
        client.get.return_value = {}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                with patch("urllib.request.urlopen"):
                    rc = snap_cmd.cmd_site_snapshot(args, client)
            # Returns 0 because partial-success is the documented behavior
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())


class TestPagesSnapshot(unittest.TestCase):
    def test_writes_pages_json_and_per_page_contents(self):
        client = _make_client()
        client.get_all.return_value = [{"id": 1}, {"id": 2}]
        client.get.side_effect = lambda path: [{"name": "body", "text": "hello"}]

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                rc = snap_cmd.cmd_pages_snapshot(args, client)
            self.assertEqual(rc, 0)
            self.assertTrue((out / "pages.json").exists())
            self.assertTrue((out / "page_1_contents.json").exists())
            self.assertTrue((out / "page_2_contents.json").exists())
            pages = json.loads((out / "pages.json").read_text())
            self.assertEqual(len(pages), 2)

    def test_per_page_error_returns_one_and_continues(self):
        client = _make_client()
        client.get_all.return_value = [{"id": 1}, {"id": 2}]

        def get_dispatch(path):
            if path == "/pages/1/contents":
                raise RuntimeError("404")
            return [{"name": "body"}]

        client.get.side_effect = get_dispatch

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "snap"
            args = MagicMock()
            args.output_dir = out
            with patch("sys.stdout"):
                rc = snap_cmd.cmd_pages_snapshot(args, client)
            # rc=1 because errors > 0, but page 2 was still saved.
            self.assertEqual(rc, 1)
            self.assertTrue((out / "page_2_contents.json").exists())
            self.assertFalse((out / "page_1_contents.json").exists())

    def test_pages_snapshot_uses_parallel_map(self):
        # Lock the contract: per-page contents fan-out goes through
        # voog._concurrency.parallel_map, with the right page ids, the right
        # max_workers, and a fetch fn that hits /pages/{pid}/contents.
        client = _make_client()
        client.get_all.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

        with patch("voog.cli.commands.snapshot.parallel_map") as mock_pmap:
            mock_pmap.return_value = []
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "snap"
                args = MagicMock()
                args.output_dir = out
                with patch("sys.stdout"):
                    snap_cmd.cmd_pages_snapshot(args, client)
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


if __name__ == "__main__":
    unittest.main()
