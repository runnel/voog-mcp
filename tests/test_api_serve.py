"""Tests for voog.api.serve — local asset auto-discovery."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from voog.api.serve import discover_local_assets


class TestDiscoverLocalAssets(unittest.TestCase):
    def test_discovers_js_and_css(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "javascripts").mkdir()
            (root / "stylesheets").mkdir()
            (root / "javascripts" / "cart.js").write_text("// js")
            (root / "javascripts" / "main.min.js").write_text("// min")
            (root / "stylesheets" / "main.css").write_text("/* css */")

            assets = discover_local_assets(root)

            self.assertEqual(assets["cart.js"], "javascripts/cart.js")
            self.assertEqual(assets["main.min.js"], "javascripts/main.min.js")
            self.assertEqual(assets["main.css"], "stylesheets/main.css")

    def test_empty_repo_returns_empty_map(self):
        with TemporaryDirectory() as tmp:
            assets = discover_local_assets(Path(tmp))
            self.assertEqual(assets, {})

    def test_only_known_extensions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "javascripts").mkdir()
            (root / "javascripts" / "ignored.txt").write_text("nope")
            (root / "javascripts" / "kept.js").write_text("yep")
            assets = discover_local_assets(root)
            self.assertEqual(list(assets), ["kept.js"])


if __name__ == "__main__":
    unittest.main()
