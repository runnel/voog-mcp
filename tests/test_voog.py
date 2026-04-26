"""Unit tests for voog.py with mocked HTTP calls."""
import os
import sys
import json as json_mod
import tempfile
import unittest
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
        """pages_list() should call /pages?per_page=250 and print each page."""
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


if __name__ == "__main__":
    unittest.main()
