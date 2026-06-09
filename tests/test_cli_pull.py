"""Tests for voog.cli.commands.pull — CLI dispatch + filesystem I/O.

Pulls layouts, layout_assets, and site data into the current working
directory, building a manifest.json. Tests verify the per-resource
shape on disk and that the manifest correctly reflects what was
written.

Network behavior (pagination, error handling) is covered by
``tests/test_client.py``; this file focuses on the CLI command's
filesystem and dispatch logic.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog.cli.commands import pull as pull_cmd


def _make_client():
    client = MagicMock()
    client.host = "example.com"
    return client


class TestPullLayouts(unittest.TestCase):
    def test_pull_writes_layout_files_and_manifest(self):
        client = _make_client()
        client.get_all.side_effect = [
            # /layouts
            [
                {"id": 1, "title": "Front", "component": False, "updated_at": "2026-01-01"},
                {"id": 2, "title": "Footer", "component": True, "updated_at": "2026-01-02"},
            ],
            # /layout_assets (empty)
            [],
        ]
        client.get.side_effect = lambda path: (
            {"id": int(path.rsplit("/", 1)[1]), "body": f"BODY-{path.rsplit('/', 1)[1]}"}
            if path.startswith("/layouts/")
            else {"settings": "ok"}
        )

        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                rc = pull_cmd.run(MagicMock(), client)
            finally:
                os.chdir(cwd_before)

            self.assertEqual(rc, 0)
            self.assertEqual(
                (Path(tmp) / "layouts" / "Front.tpl").read_text(),
                "BODY-1",
            )
            self.assertEqual(
                (Path(tmp) / "components" / "Footer.tpl").read_text(),
                "BODY-2",
            )

            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertIn("layouts/Front.tpl", manifest)
            self.assertEqual(manifest["layouts/Front.tpl"]["id"], 1)
            self.assertEqual(manifest["layouts/Front.tpl"]["type"], "layout")
            self.assertIn("components/Footer.tpl", manifest)

    def test_pull_skips_binary_assets(self):
        # Binary assets (no `data` field) must not produce filesystem
        # entries — they don't round-trip via the API. Text assets do.
        client = _make_client()
        client.get_all.side_effect = [
            [],  # /layouts
            [
                {
                    "id": 10,
                    "filename": "main.css",
                    "kind": "stylesheet",
                    "data": "body { color: red; }",
                    "updated_at": "2026-01-01",
                },
                {
                    "id": 11,
                    "filename": "logo.png",
                    "kind": "image",
                    "data": None,  # binary — skip
                    "updated_at": "2026-01-01",
                },
            ],
        ]
        client.get.return_value = {"settings": "ok"}

        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                rc = pull_cmd.run(MagicMock(), client)
            finally:
                os.chdir(cwd_before)

            self.assertEqual(rc, 0)
            css = Path(tmp) / "stylesheets" / "main.css"
            self.assertTrue(css.exists())
            self.assertEqual(css.read_text(), "body { color: red; }")
            # Binary skipped
            self.assertFalse((Path(tmp) / "images" / "logo.png").exists())

            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertIn("stylesheets/main.css", manifest)
            self.assertNotIn("images/logo.png", manifest)

    def test_pull_fetches_editable_asset_data_via_detail_endpoint(self):
        # Regression (2026-06, observed against stellasoomlais.com): the
        # /layout_assets LIST endpoint stopped including `data` and `kind`
        # — it now carries `asset_type` + `editable`, and `data` exists
        # only on the /layout_assets/{id} detail endpoint. Pre-fix pull
        # misread every asset as binary, skipped all of them, and wrote a
        # layouts-only manifest — which then broke
        # `voog push javascripts/*.js` ("not in manifest. Skipping.").
        client = _make_client()
        client.get_all.side_effect = [
            [],  # /layouts
            [
                {
                    "id": 10,
                    "filename": "stella-id.js",
                    "asset_type": "javascript",
                    "editable": True,
                    "updated_at": "2026-06-01",
                },
                {
                    "id": 11,
                    "filename": "logo.png",
                    "asset_type": "image",
                    "editable": False,
                    "updated_at": "2026-06-01",
                },
            ],
        ]

        def fake_get(path):
            if path == "/layout_assets/10":
                return {"id": 10, "data": "console.log('hi');"}
            self.assertNotEqual(
                path, "/layout_assets/11", "binary asset must not be detail-fetched"
            )
            return {"settings": "ok"}

        client.get.side_effect = fake_get

        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                rc = pull_cmd.run(MagicMock(), client)
            finally:
                os.chdir(cwd_before)

            self.assertEqual(rc, 0)
            js = Path(tmp) / "javascripts" / "stella-id.js"
            self.assertTrue(js.exists())
            self.assertEqual(js.read_text(), "console.log('hi');")

            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertIn("javascripts/stella-id.js", manifest)
            entry = manifest["javascripts/stella-id.js"]
            self.assertEqual(entry["id"], 10)
            self.assertEqual(entry["type"], "asset")
            self.assertEqual(entry["kind"], "javascript")
            # Binary still skipped — neither on disk nor in the manifest.
            self.assertFalse((Path(tmp) / "images" / "logo.png").exists())
            self.assertNotIn("images/logo.png", manifest)

    def test_pull_writes_site_data(self):
        client = _make_client()
        client.get_all.side_effect = [[], []]  # no layouts, no assets
        client.get.return_value = {"name": "My Site", "default_language": "en"}

        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                rc = pull_cmd.run(MagicMock(), client)
            finally:
                os.chdir(cwd_before)

            self.assertEqual(rc, 0)
            site_data = json.loads((Path(tmp) / "site-data.json").read_text())
            self.assertEqual(site_data["name"], "My Site")


if __name__ == "__main__":
    unittest.main()
