"""Unit tests for voog.py with mocked HTTP calls."""
import os
import sys
import json as json_mod
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

# Make voog importable
TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))
import voog


class TestModuleImport(unittest.TestCase):
    def test_module_imports_without_site_config(self):
        """voog.py should import without voog-site.json in cwd."""
        self.assertIsNone(voog.SITE_CONFIG)
        self.assertEqual(voog.HOST, "")
        self.assertEqual(voog.BASE_URL, "")

    def test_help_command_works_without_config(self):
        """`voog.py help` should work without site config (regression test)."""
        with patch.object(sys, "argv", ["voog.py", "help"]):
            with patch("builtins.print") as mock_print:
                voog.main()
                # main() should print docstring and return
                self.assertTrue(mock_print.called)


class TestPagesList(unittest.TestCase):
    def test_pages_list_calls_api_and_prints_each(self):
        """pages_list() should call /pages and print each page."""
        fake_pages = [
            {"id": 152377, "path": "", "title": "Foto", "hidden": False, "layout_name": "Front page"},
            {"id": 1523073, "path": "foto", "title": "Blog", "hidden": True, "layout_name": "Blog & news"},
        ]
        with patch.object(voog, "api_get_all", return_value=fake_pages) as mock_api:
            with patch("builtins.print") as mock_print:
                voog.pages_list()
                mock_api.assert_called_once_with("/pages")
                output = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
                self.assertIn("152377", output)
                self.assertIn("Foto", output)
                self.assertIn("1523073", output)
                self.assertIn("hidden", output.lower())


class TestPageGet(unittest.TestCase):
    def test_page_get_calls_api_and_prints_details(self):
        fake_page = {
            "id": 152377,
            "path": "",
            "title": "Foto",
            "hidden": False,
            "layout_id": 977702,
            "layout_name": "Front page",
            "content_type": "blog",
            "language": {"code": "et", "id": 6580},
            "parent_id": None,
        }
        with patch.object(voog, "api_get", return_value=fake_page) as mock_api:
            with patch("builtins.print") as mock_print:
                voog.page_get("152377")
                mock_api.assert_called_once_with("/pages/152377")
                output = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
                self.assertIn("Foto", output)
                self.assertIn("977702", output)
                self.assertIn("blog", output)
                self.assertIn("et", output)


class TestPagesSnapshot(unittest.TestCase):
    def test_snapshot_writes_pages_index_and_contents(self):
        fake_pages = [
            {"id": 100, "path": "foo", "title": "Foo"},
            {"id": 200, "path": "bar", "title": "Bar"},
        ]
        fake_contents = [
            {"id": 1, "name": "body", "content_type": "text"},
        ]

        def mock_api_get(path, *args, **kwargs):
            if path.endswith("/contents"):
                return fake_contents
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(voog, "api_get_all", return_value=fake_pages):
                with patch.object(voog, "api_get", side_effect=mock_api_get):
                    voog.pages_snapshot(tmpdir)

            snap_path = Path(tmpdir) / "pages.json"
            self.assertTrue(snap_path.exists())
            saved = json_mod.loads(snap_path.read_text())
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]["id"], 100)

            # Contents per page saved separately
            self.assertTrue((Path(tmpdir) / "page_100_contents.json").exists())
            self.assertTrue((Path(tmpdir) / "page_200_contents.json").exists())


class TestLayoutRename(unittest.TestCase):
    def test_layout_rename_calls_api_and_updates_manifest_and_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "layouts").mkdir()
            old_file = tmpdir / "layouts" / "Front page.tpl"
            old_file.write_text("<html>Front page</html>", encoding="utf-8")

            manifest = {
                "layouts/Front page.tpl": {"id": 977702, "type": "layout"},
            }
            (tmpdir / "manifest.json").write_text(json_mod.dumps(manifest), encoding="utf-8")

            with patch.object(voog, "LOCAL_DIR", tmpdir):
                with patch.object(voog, "api_put") as mock_put:
                    mock_put.return_value = {"id": 977702, "title": "old-Front page"}
                    voog.layout_rename(977702, "old-Front page")

            # API called
            mock_put.assert_called_once_with(
                "/layouts/977702", {"title": "old-Front page"}
            )
            # File renamed
            self.assertFalse(old_file.exists())
            self.assertTrue((tmpdir / "layouts" / "old-Front page.tpl").exists())
            # Manifest updated
            new_manifest = json_mod.loads((tmpdir / "manifest.json").read_text())
            self.assertNotIn("layouts/Front page.tpl", new_manifest)
            self.assertIn("layouts/old-Front page.tpl", new_manifest)
            self.assertEqual(new_manifest["layouts/old-Front page.tpl"]["id"], 977702)


class TestLayoutRenameValidation(unittest.TestCase):
    # NB: patch wraps assertRaises so assert_not_called runs *after* SystemExit
    # is caught but *before* the patch is torn down — otherwise the assertion
    # is unreachable and the test gives false confidence.
    def test_rejects_slash_in_title(self):
        with patch.object(voog, "api_put") as mock_put:
            with self.assertRaises(SystemExit):
                voog.layout_rename(977702, "foo/bar")
            mock_put.assert_not_called()

    def test_rejects_backslash_in_title(self):
        with patch.object(voog, "api_put") as mock_put:
            with self.assertRaises(SystemExit):
                voog.layout_rename(977702, "foo\\bar")
            mock_put.assert_not_called()

    def test_rejects_dot_prefix(self):
        with patch.object(voog, "api_put") as mock_put:
            with self.assertRaises(SystemExit):
                voog.layout_rename(977702, ".hidden")
            mock_put.assert_not_called()


class TestPageSetHidden(unittest.TestCase):
    def test_page_set_hidden_true_calls_api_for_each_id(self):
        with patch.object(voog, "api_put") as mock_put:
            mock_put.return_value = {}
            voog.page_set_hidden(["100", "200", "300"], True)

            self.assertEqual(mock_put.call_count, 3)
            mock_put.assert_any_call("/pages/100", {"hidden": True})
            mock_put.assert_any_call("/pages/200", {"hidden": True})
            mock_put.assert_any_call("/pages/300", {"hidden": True})

    def test_page_set_hidden_false_unhides(self):
        with patch.object(voog, "api_put") as mock_put:
            mock_put.return_value = {}
            voog.page_set_hidden(["100"], False)
            mock_put.assert_called_once_with("/pages/100", {"hidden": False})


class TestPageSetLayout(unittest.TestCase):
    def test_page_set_layout_calls_put_with_layout_id(self):
        with patch.object(voog, "api_put") as mock_put:
            mock_put.return_value = {}
            voog.page_set_layout("152377", "977702")
            mock_put.assert_called_once_with("/pages/152377", {"layout_id": 977702})


class TestPageDelete(unittest.TestCase):
    def test_page_delete_with_force_calls_api_without_prompt(self):
        with patch.object(voog, "api_delete") as mock_del:
            mock_del.return_value = None
            voog.page_delete("123", force=True)
            mock_del.assert_called_once_with("/pages/123")

    def test_page_delete_without_force_prompts_user(self):
        with patch.object(voog, "api_delete") as mock_del:
            with patch.object(voog, "api_get", return_value={"title": "Test", "path": "test"}):
                with patch("builtins.input", return_value="j"):
                    voog.page_delete("123", force=False)
                    mock_del.assert_called_once_with("/pages/123")

    def test_page_delete_aborts_if_user_says_no(self):
        with patch.object(voog, "api_delete") as mock_del:
            with patch.object(voog, "api_get", return_value={"title": "Test", "path": "test"}):
                with patch("builtins.input", return_value="e"):
                    voog.page_delete("123", force=False)
                    mock_del.assert_not_called()


class TestPagesPull(unittest.TestCase):
    def test_pages_pull_writes_simplified_pages_json(self):
        fake_pages = [
            {"id": 100, "path": "foo", "title": "Foo", "hidden": False,
             "layout_id": 1, "parent_id": None, "language": {"code": "et"}},
            {"id": 200, "path": "bar", "title": "Bar", "hidden": True,
             "layout_id": 2, "parent_id": 100, "language": {"code": "et"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(voog, "LOCAL_DIR", Path(tmpdir)):
                with patch.object(voog, "api_get_all", return_value=fake_pages):
                    voog.pages_pull()

            saved = json_mod.loads((Path(tmpdir) / "pages.json").read_text())
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]["id"], 100)
            self.assertEqual(saved[0]["language_code"], "et")
            self.assertIn("layout_id", saved[0])


class TestSlugifyPath(unittest.TestCase):
    def test_empty_path_becomes_home(self):
        self.assertEqual(voog._slugify_path(""), "home")
        self.assertEqual(voog._slugify_path("/"), "home")
        self.assertEqual(voog._slugify_path(None), "home")

    def test_simple_path(self):
        self.assertEqual(voog._slugify_path("foto"), "foto")
        self.assertEqual(voog._slugify_path("/foto"), "foto")

    def test_nested_path_uses_dashes(self):
        self.assertEqual(voog._slugify_path("/pood/kass"), "pood-kass")
        self.assertEqual(voog._slugify_path("pood/kass/"), "pood-kass")


class TestPickSamplePagePaths(unittest.TestCase):
    def test_returns_empty_for_no_pages(self):
        self.assertEqual(voog._pick_sample_page_paths([]), [])

    def test_picks_front_first_then_variety(self):
        pages = [
            {"id": 1, "path": "", "content_type": "common", "hidden": False},
            {"id": 2, "path": "info", "content_type": "common", "hidden": False},
            {"id": 3, "path": "pood/kass", "content_type": "product", "hidden": False},
            {"id": 4, "path": "blog", "content_type": "blog", "hidden": False},
        ]
        picks = voog._pick_sample_page_paths(pages, max_samples=3)
        self.assertEqual(len(picks), 3)
        self.assertEqual(picks[0], "/")  # front first
        # Remaining 2 should be from different content_types
        self.assertIn("/pood/kass", picks)
        self.assertIn("/blog", picks)

    def test_skips_hidden_pages(self):
        pages = [
            {"id": 1, "path": "", "content_type": "common", "hidden": False},
            {"id": 2, "path": "secret", "content_type": "common", "hidden": True},
        ]
        picks = voog._pick_sample_page_paths(pages, max_samples=3)
        self.assertNotIn("/secret", picks)


class TestSiteSnapshot(unittest.TestCase):
    """Comprehensive snapshot of every mutable Voog resource (TDD spec)."""

    def _default_responses(self):
        return {
            "lists": {
                "/pages": [
                    {"id": 100, "path": "", "title": "Front",
                     "content_type": "common", "hidden": False},
                    {"id": 200, "path": "info", "title": "Info",
                     "content_type": "common", "hidden": False},
                ],
                "/articles": [{"id": 500, "title": "A1"}],
                "/layouts": [{"id": 1, "title": "x"}],
                "/layout_assets": [{"id": 10, "filename": "a.css"}],
                "/languages": [{"code": "et"}],
                "/redirect_rules": [],
                "/tags": [],
                "/forms": [],
                "/media_sets": [],
                "/assets": [],
                "/webhooks": [],
                "/elements": [],
                "/element_definitions": [],
                "/content_partials": [],
                "/nodes": [],
                "/texts": [],
            },
            "singletons": {
                "/site": {"id": 1, "name": "Site"},
                "/me": {"email": "a@b.c"},
            },
            "by_path": {
                "/pages/100/contents": [{"id": 1, "name": "body"}],
                "/pages/200/contents": [],
                "/articles/500": {"id": 500, "body": "hello"},
                "/products/9000": {"id": 9000, "translations": {}},
            },
            "products": [{"id": 9000, "name": "P1"}],
        }

    def _patches(self, responses, *, missing_404=()):
        lists = responses["lists"]
        singletons = responses["singletons"]
        by_path = responses["by_path"]
        products = responses.get("products", [])
        ecommerce_base = "https://example.com/admin/api/ecommerce/v1"

        def get_all(path, *args, base=None, **kwargs):
            if path in missing_404:
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            if base == ecommerce_base and path == "/products":
                return products
            return lists.get(path, [])

        def get(path, *args, base=None, **kwargs):
            if path in missing_404:
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            if path in singletons:
                return singletons[path]
            return by_path.get(path, {})

        return get_all, get

    def _run(self, *, responses=None, missing_404=(), html_pages=None,
             html_error=False, dirname="snap"):
        responses = responses or self._default_responses()
        get_all, get = self._patches(responses, missing_404=missing_404)
        html_pages = html_pages or {}

        def fetch(url):
            if html_error:
                raise urllib.error.URLError("connection refused")
            return html_pages.get(url, "<html><style data-voog-style></style></html>")

        parent = tempfile.mkdtemp()
        outdir = Path(parent) / dirname
        with patch.object(voog, "HOST", "example.com"), \
             patch.object(voog, "ECOMMERCE_URL",
                          "https://example.com/admin/api/ecommerce/v1"), \
             patch.object(voog, "api_get_all", side_effect=get_all), \
             patch.object(voog, "api_get", side_effect=get), \
             patch.object(voog, "_fetch_rendered_page", side_effect=fetch), \
             patch("builtins.print") as mock_print:
            voog.site_snapshot(str(outdir))
        return outdir, mock_print

    def test_refuses_to_overwrite_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "existing-snap"
            existing.mkdir()
            with patch.object(voog, "HOST", "example.com"):
                with self.assertRaises(SystemExit):
                    voog.site_snapshot(str(existing))

    def test_writes_top_level_resource_lists(self):
        outdir, _ = self._run()
        for filename in (
            "pages.json", "articles.json", "layouts.json", "layout_assets.json",
            "languages.json", "redirect_rules.json", "tags.json", "forms.json",
            "media_sets.json", "assets.json", "webhooks.json",
            "elements.json", "element_definitions.json",
            "content_partials.json", "nodes.json", "texts.json",
        ):
            self.assertTrue((outdir / filename).exists(), f"Missing {filename}")

        pages = json_mod.loads((outdir / "pages.json").read_text())
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["id"], 100)

    def test_writes_singletons(self):
        outdir, _ = self._run()
        site = json_mod.loads((outdir / "site.json").read_text())
        self.assertEqual(site["name"], "Site")
        me = json_mod.loads((outdir / "me.json").read_text())
        self.assertEqual(me["email"], "a@b.c")

    def test_writes_per_page_contents_files(self):
        outdir, _ = self._run()
        self.assertTrue((outdir / "page_100_contents.json").exists())
        self.assertTrue((outdir / "page_200_contents.json").exists())
        contents_100 = json_mod.loads((outdir / "page_100_contents.json").read_text())
        self.assertEqual(contents_100[0]["name"], "body")

    def test_writes_per_article_detail_files(self):
        outdir, _ = self._run()
        self.assertTrue((outdir / "article_500.json").exists())
        article = json_mod.loads((outdir / "article_500.json").read_text())
        self.assertEqual(article["body"], "hello")

    def test_writes_per_product_files_with_include(self):
        """Per-product calls api_get with include=variant_types,translations."""
        responses = self._default_responses()
        get_all, _ = self._patches(responses)

        captured_calls = []

        def get(path, *args, base=None, **kwargs):
            captured_calls.append((path, args, kwargs, base))
            if path == "/products/9000":
                return responses["by_path"]["/products/9000"]
            if path in responses["singletons"]:
                return responses["singletons"][path]
            return responses["by_path"].get(path, {})

        parent = tempfile.mkdtemp()
        outdir = Path(parent) / "snap"
        with patch.object(voog, "HOST", "example.com"), \
             patch.object(voog, "ECOMMERCE_URL",
                          "https://example.com/admin/api/ecommerce/v1"), \
             patch.object(voog, "api_get_all", side_effect=get_all), \
             patch.object(voog, "api_get", side_effect=get), \
             patch.object(voog, "_fetch_rendered_page",
                          return_value="<html></html>"), \
             patch("builtins.print"):
            voog.site_snapshot(str(outdir))

        self.assertTrue((outdir / "products.json").exists())
        self.assertTrue((outdir / "product_9000.json").exists())

        product_calls = [c for c in captured_calls if c[0] == "/products/9000"]
        self.assertEqual(len(product_calls), 1)
        path, args, kwargs, base = product_calls[0]
        params = args[0] if args else kwargs.get("params")
        self.assertEqual(params, {"include": "variant_types,translations"})
        self.assertEqual(base, "https://example.com/admin/api/ecommerce/v1")

    def test_skips_404_endpoints_without_aborting(self):
        outdir, mock_print = self._run(missing_404=("/nodes", "/elements"))
        # Skipped files do NOT exist
        self.assertFalse((outdir / "nodes.json").exists())
        self.assertFalse((outdir / "elements.json").exists())
        # But other files still written — and summary still printed
        self.assertTrue((outdir / "pages.json").exists())
        output = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
        self.assertIn("Snapshot complete", output)

    def test_empty_list_endpoint_writes_empty_array(self):
        """Empty list still writes `[]` so absence vs empty is distinguishable."""
        outdir, _ = self._run()
        # /tags returns [] in defaults
        self.assertTrue((outdir / "tags.json").exists())
        tags = json_mod.loads((outdir / "tags.json").read_text())
        self.assertEqual(tags, [])

    def test_writes_rendered_html_for_sample_pages(self):
        """Rendered HTML files are written via _fetch_rendered_page."""
        html = '<html><style data-voog-style>:root{--c:red}</style></html>'
        outdir, _ = self._run(html_pages={
            "https://example.com/": html,
            "https://example.com/info": html,
        })
        # Front page rendered as voog_style_rendered_home.html
        self.assertTrue((outdir / "voog_style_rendered_home.html").exists())
        rendered = (outdir / "voog_style_rendered_home.html").read_text()
        self.assertIn("data-voog-style", rendered)

    def test_html_fetch_failure_does_not_abort(self):
        outdir, mock_print = self._run(html_error=True)
        # Other files still written
        self.assertTrue((outdir / "pages.json").exists())
        # But no rendered HTML files
        self.assertEqual(list(outdir.glob("voog_style_rendered_*.html")), [])

    def test_summary_count_matches_files_written(self):
        outdir, mock_print = self._run()
        output = "\n".join(str(c.args[0]) for c in mock_print.call_args_list)
        # Find the "Snapshot complete: N resources" line
        import re as _re
        m = _re.search(r"Snapshot complete:\s*(\d+)\s+resources", output)
        self.assertIsNotNone(m, f"Summary not found in:\n{output}")
        claimed = int(m.group(1))
        actual = len([f for f in outdir.iterdir() if f.is_file()])
        self.assertEqual(claimed, actual,
                         f"Summary claims {claimed} but {actual} files exist")


class TestEcommerceWrapper(unittest.TestCase):
    def setUp(self):
        # Save original module-level globals so tests don't leak state
        self._orig_base = voog.BASE_URL
        self._orig_ecommerce = voog.ECOMMERCE_URL

    def tearDown(self):
        voog.BASE_URL = self._orig_base
        voog.ECOMMERCE_URL = self._orig_ecommerce

    def test_products_list_uses_ecommerce_base(self):
        """products_list() must call api_get with base=ECOMMERCE_URL."""
        voog.ECOMMERCE_URL = "https://runnel.ee/admin/api/ecommerce/v1"
        voog.BASE_URL = "https://runnel.ee/admin/api"

        fake_products = [{"id": 1, "name": "Test", "slug": "test", "status": "live"}]
        with patch.object(voog, "api_get", return_value=fake_products) as mock_api:
            with patch("builtins.print"):
                voog.products_list()
            # Verify base keyword argument was ECOMMERCE_URL on first call
            call_kwargs = mock_api.call_args.kwargs
            self.assertEqual(call_kwargs.get("base"), voog.ECOMMERCE_URL)

    def test_product_get_uses_ecommerce_base(self):
        """product_get() must call api_get with base=ECOMMERCE_URL."""
        voog.ECOMMERCE_URL = "https://runnel.ee/admin/api/ecommerce/v1"
        fake_product = {"id": 247217, "name": "Kass", "slug": "kass", "sku": "", "status": "live", "translations": {}}
        with patch.object(voog, "api_get", return_value=fake_product) as mock_api:
            with patch("builtins.print"):
                voog.product_get("247217")
            call_kwargs = mock_api.call_args.kwargs
            self.assertEqual(call_kwargs.get("base"), voog.ECOMMERCE_URL)

    def test_product_update_uses_ecommerce_base(self):
        """product_update() must call api_put and api_get with base=ECOMMERCE_URL."""
        voog.ECOMMERCE_URL = "https://runnel.ee/admin/api/ecommerce/v1"
        fake_result = {"id": 1, "name": "Uus nimi", "slug": "uus-nimi", "translations": {}}
        with patch.object(voog, "api_put", return_value=fake_result) as mock_put:
            with patch.object(voog, "api_get", return_value=fake_result):
                with patch("builtins.print"):
                    voog.product_update("1", [("name-et", "Uus nimi")])
                put_kwargs = mock_put.call_args.kwargs
                self.assertEqual(put_kwargs.get("base"), voog.ECOMMERCE_URL)


class TestAssetReplace(unittest.TestCase):
    """asset-replace = DELETE+POST workaround for layout_asset filename change.

    Voog API rejects PUT /layout_assets/{id} with `filename` field (HTTP 500).
    Workaround: GET old, POST new (new id), update manifest + local file.
    Old asset is NOT auto-deleted — caller must update templates first.
    """

    def _make_workspace(self, tmpdir, *, content="body { color: red; }",
                        old_filename="main.css", folder="stylesheets",
                        asset_type="stylesheet", asset_id=1833688,
                        extra_manifest_entries=None):
        """Build a temp workspace with one asset + optional extras."""
        tmpdir = Path(tmpdir)
        (tmpdir / folder).mkdir(parents=True, exist_ok=True)
        (tmpdir / folder / old_filename).write_text(content, encoding="utf-8")
        manifest = {
            f"{folder}/{old_filename}": {
                "id": asset_id,
                "type": "layout_asset",
                "asset_type": asset_type,
            },
        }
        if extra_manifest_entries:
            manifest.update(extra_manifest_entries)
        (tmpdir / "manifest.json").write_text(
            json_mod.dumps(manifest), encoding="utf-8"
        )
        return tmpdir

    def test_happy_path_get_post_rename_update_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = self._make_workspace(tmpdir)
            with patch.object(voog, "LOCAL_DIR", tmpdir), \
                 patch.object(voog, "api_get") as mock_get, \
                 patch.object(voog, "api_post") as mock_post, \
                 patch.object(voog, "api_delete") as mock_delete:
                mock_get.return_value = {
                    "id": 1833688, "filename": "main.css",
                    "asset_type": "stylesheet", "data": "body { color: red; }",
                }
                mock_post.return_value = {
                    "id": 2627811, "filename": "old-main.css",
                    "asset_type": "stylesheet",
                }
                voog.asset_replace(1833688, "old-main.css")

            mock_get.assert_called_once_with("/layout_assets/1833688")
            mock_post.assert_called_once()
            mock_delete.assert_not_called()  # spec: do NOT auto-delete

            # Local file renamed
            self.assertFalse((tmpdir / "stylesheets" / "main.css").exists())
            self.assertTrue((tmpdir / "stylesheets" / "old-main.css").exists())
            self.assertEqual(
                (tmpdir / "stylesheets" / "old-main.css").read_text(),
                "body { color: red; }",
            )

            # Manifest: old removed, new entry has NEW id
            manifest = json_mod.loads((tmpdir / "manifest.json").read_text())
            self.assertNotIn("stylesheets/main.css", manifest)
            self.assertIn("stylesheets/old-main.css", manifest)
            self.assertEqual(manifest["stylesheets/old-main.css"]["id"], 2627811)
            self.assertEqual(
                manifest["stylesheets/old-main.css"]["asset_type"], "stylesheet"
            )
            self.assertEqual(
                manifest["stylesheets/old-main.css"]["type"], "layout_asset"
            )

    def test_post_payload_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = self._make_workspace(tmpdir)
            with patch.object(voog, "LOCAL_DIR", tmpdir), \
                 patch.object(voog, "api_get") as mock_get, \
                 patch.object(voog, "api_post") as mock_post:
                mock_get.return_value = {
                    "id": 1833688, "filename": "main.css",
                    "asset_type": "stylesheet", "data": "body { color: red; }",
                }
                mock_post.return_value = {"id": 2627811}
                voog.asset_replace(1833688, "old-main.css")

            args, _ = mock_post.call_args
            self.assertEqual(args[0], "/layout_assets")
            payload = args[1]
            self.assertEqual(payload["filename"], "old-main.css")
            self.assertEqual(payload["asset_type"], "stylesheet")
            self.assertEqual(payload["data"], "body { color: red; }")

    def test_data_falls_back_to_local_file_when_get_lacks_data(self):
        """If GET response has no `data`, content is read from local disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = self._make_workspace(
                tmpdir, content="// from local disk", old_filename="app.js",
                folder="javascripts", asset_type="javascript",
            )
            with patch.object(voog, "LOCAL_DIR", tmpdir), \
                 patch.object(voog, "api_get") as mock_get, \
                 patch.object(voog, "api_post") as mock_post:
                # GET returns metadata WITHOUT data field
                mock_get.return_value = {
                    "id": 1833688, "filename": "app.js",
                    "asset_type": "javascript",
                }
                mock_post.return_value = {"id": 2627811}
                voog.asset_replace(1833688, "old-app.js")

            args, _ = mock_post.call_args
            self.assertEqual(args[1]["data"], "// from local disk")

    def test_other_manifest_entries_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            extras = {
                "stylesheets/cart.css": {
                    "id": 1111, "type": "layout_asset", "asset_type": "stylesheet",
                },
                "layouts/Front page.tpl": {"id": 977702, "type": "layout"},
            }
            tmpdir = self._make_workspace(tmpdir, extra_manifest_entries=extras)
            with patch.object(voog, "LOCAL_DIR", tmpdir), \
                 patch.object(voog, "api_get") as mock_get, \
                 patch.object(voog, "api_post") as mock_post:
                mock_get.return_value = {
                    "id": 1833688, "filename": "main.css",
                    "asset_type": "stylesheet", "data": "x",
                }
                mock_post.return_value = {"id": 2627811}
                voog.asset_replace(1833688, "old-main.css")

            manifest = json_mod.loads((tmpdir / "manifest.json").read_text())
            self.assertEqual(manifest["stylesheets/cart.css"]["id"], 1111)
            self.assertEqual(
                manifest["layouts/Front page.tpl"]["id"], 977702
            )


class TestAssetReplaceValidation(unittest.TestCase):
    # NB: assertions inside `with patch` so assert_not_called runs while patch active
    def test_rejects_slash_in_filename(self):
        with patch.object(voog, "api_get") as mock_get, \
             patch.object(voog, "api_post") as mock_post:
            with self.assertRaises(SystemExit):
                voog.asset_replace(1833688, "foo/bar.css")
            mock_get.assert_not_called()
            mock_post.assert_not_called()

    def test_rejects_backslash_in_filename(self):
        with patch.object(voog, "api_get") as mock_get, \
             patch.object(voog, "api_post") as mock_post:
            with self.assertRaises(SystemExit):
                voog.asset_replace(1833688, "foo\\bar.css")
            mock_get.assert_not_called()
            mock_post.assert_not_called()

    def test_rejects_leading_dot(self):
        # Covers both `.hidden.css` and `..parent.css`
        with patch.object(voog, "api_get") as mock_get, \
             patch.object(voog, "api_post") as mock_post:
            with self.assertRaises(SystemExit):
                voog.asset_replace(1833688, ".hidden.css")
            mock_get.assert_not_called()
            mock_post.assert_not_called()

    def test_rejects_double_dot_prefix(self):
        with patch.object(voog, "api_get") as mock_get, \
             patch.object(voog, "api_post") as mock_post:
            with self.assertRaises(SystemExit):
                voog.asset_replace(1833688, "..parent.css")
            mock_get.assert_not_called()
            mock_post.assert_not_called()


class TestPageCreate(unittest.TestCase):
    def test_page_create_calls_api_post_with_required_fields(self):
        with patch.object(voog, "api_post") as mock_post:
            mock_post.return_value = {
                "id": 999999,
                "title": "Privacy",
                "slug": "privacy",
                "path": "privacy",
                "language_id": 629624,
                "layout_id": 12345,
                "hidden": True,
                "state": "draft",
            }
            result = voog.page_create(
                title="Privacy",
                slug="privacy",
                language_id=629624,
                layout_id=12345,
                hidden=True,
                state="draft",
            )

        mock_post.assert_called_once_with(
            "/pages",
            {
                "title": "Privacy",
                "slug": "privacy",
                "language_id": 629624,
                "layout_id": 12345,
                "hidden": True,
                "state": "draft",
            },
        )
        self.assertEqual(result["id"], 999999)
        self.assertEqual(result["slug"], "privacy")

    def test_page_create_omits_none_optional_fields(self):
        """parent_id, layout_id, state are optional — None values must NOT be sent."""
        with patch.object(voog, "api_post") as mock_post:
            mock_post.return_value = {"id": 1, "title": "T", "slug": "t"}
            voog.page_create(
                title="T",
                slug="t",
                language_id=629624,
            )
        mock_post.assert_called_once_with(
            "/pages",
            {"title": "T", "slug": "t", "language_id": 629624},
        )

    def test_page_create_validates_required_args(self):
        """Missing required arg → TypeError, no API call."""
        with patch.object(voog, "api_post") as mock_post:
            with self.assertRaises(TypeError):
                voog.page_create(slug="x", language_id=1)  # missing title
            mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
