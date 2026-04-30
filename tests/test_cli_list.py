"""Tests for voog.cli.commands.list — manifest listing.

Tiny command: reads manifest.json, prints a sorted summary. Tests
cover the manifest-missing path and the happy-path output ordering.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import list as list_cmd


class TestListMissingManifest(unittest.TestCase):
    def test_missing_manifest_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    rc = list_cmd.run(MagicMock(), MagicMock())
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 1)
        self.assertIn("manifest.json missing", stderr.getvalue())


class TestListManifestEntries(unittest.TestCase):
    def test_listing_shows_entries_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = {
                "layouts/B.tpl": {"id": 2, "type": "layout"},
                "layouts/A.tpl": {"id": 1, "type": "layout"},
                "stylesheets/main.css": {"id": 3, "type": "asset"},
            }
            (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    rc = list_cmd.run(MagicMock(), MagicMock())
            finally:
                os.chdir(cwd_before)
            out = stdout.getvalue()
        self.assertEqual(rc, 0)
        # Sorted = A before B; main.css last
        a_pos = out.find("A.tpl")
        b_pos = out.find("B.tpl")
        css_pos = out.find("main.css")
        self.assertGreater(a_pos, 0)
        self.assertGreater(b_pos, a_pos)
        self.assertGreater(css_pos, b_pos)


if __name__ == "__main__":
    unittest.main()
