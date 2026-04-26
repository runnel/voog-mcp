"""Unit tests for voog.py with mocked HTTP calls."""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
