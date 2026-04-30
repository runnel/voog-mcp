"""Tests for voog.cli.commands.layouts — rename, asset-replace, layout-create.

The rename and create commands also touch the manifest + filesystem;
tests cover both API-side and disk-side behavior. asset-replace POSTs
a new asset (Voog's PUT-on-filename has a bug) and surfaces a curl
snippet asking the user to delete the old one manually — no DELETE
is issued automatically.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import layouts as layouts_cmd


def _make_client():
    client = MagicMock()
    client.host = "example.com"
    return client


def _args(**kwargs):
    args = MagicMock()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


class TestLayoutRename(unittest.TestCase):
    def test_rename_updates_api_manifest_and_file(self):
        client = _make_client()
        client.put.return_value = {"id": 1, "title": "Hero"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            (tmp_path / "layouts" / "Old.tpl").write_text("body", encoding="utf-8")
            (tmp_path / "manifest.json").write_text(
                json.dumps({"layouts/Old.tpl": {"id": 1, "type": "layout", "updated_at": ""}}),
                encoding="utf-8",
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = layouts_cmd.cmd_layout_rename(_args(layout_id=1, new_title="Hero"), client)
            finally:
                os.chdir(cwd_before)

            # Assertions must run while tmp still exists.
            self.assertEqual(rc, 0)
            client.put.assert_called_once_with("/layouts/1", {"title": "Hero"})
            self.assertFalse((tmp_path / "layouts" / "Old.tpl").exists())
            self.assertTrue((tmp_path / "layouts" / "Hero.tpl").exists())
            manifest = json.loads((tmp_path / "manifest.json").read_text())
            self.assertNotIn("layouts/Old.tpl", manifest)
            self.assertIn("layouts/Hero.tpl", manifest)

    def test_rename_with_path_separator_rejected(self):
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = layouts_cmd.cmd_layout_rename(_args(layout_id=1, new_title="evil/path"), client)
        self.assertEqual(rc, 2)
        self.assertIn("must not contain", stderr.getvalue())
        client.put.assert_not_called()

    def test_rename_with_dot_prefix_rejected(self):
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO):
            rc = layouts_cmd.cmd_layout_rename(_args(layout_id=1, new_title=".hidden"), client)
        self.assertEqual(rc, 2)
        client.put.assert_not_called()

    def test_rename_with_no_manifest_only_updates_api(self):
        client = _make_client()
        client.put.return_value = {"id": 1}
        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = layouts_cmd.cmd_layout_rename(_args(layout_id=1, new_title="Hero"), client)
            finally:
                os.chdir(cwd_before)
        # No manifest = API-only update is OK (warning printed)
        self.assertEqual(rc, 0)
        client.put.assert_called_once()


class TestAssetReplace(unittest.TestCase):
    def test_replace_posts_new_asset_and_renames_local_file(self):
        # Manifest fixture mirrors what `pull.py` actually writes:
        # ``type="asset"`` (not "layout_asset"). A previous test masked
        # a real source bug by fabricating "layout_asset" entries — the
        # match fell through and the local file/manifest update branch
        # silently never ran in production.
        client = _make_client()
        client.get.return_value = {
            "asset_type": "stylesheet",
            "filename": "old.css",
            "data": "body { color: red; }",
        }
        client.post.return_value = {"id": 99}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "stylesheets").mkdir()
            (tmp_path / "stylesheets" / "old.css").write_text("x", encoding="utf-8")
            (tmp_path / "manifest.json").write_text(
                json.dumps(
                    {
                        "stylesheets/old.css": {
                            "id": 5,
                            "type": "asset",
                            "kind": "stylesheet",
                        }
                    }
                ),
                encoding="utf-8",
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = layouts_cmd.cmd_asset_replace(
                        _args(asset_id=5, new_filename="new.css"), client
                    )
            finally:
                os.chdir(cwd_before)

            self.assertEqual(rc, 0)
            # POST payload must carry filename + asset_type + content;
            # a swap would silently corrupt the upload (Voog dispatches
            # by asset_type, the data goes wherever filename says).
            client.post.assert_called_once_with(
                "/layout_assets",
                {
                    "filename": "new.css",
                    "asset_type": "stylesheet",
                    "data": "body { color: red; }",
                },
            )
            # File rename + manifest update branch ran (the bug guard).
            self.assertTrue((tmp_path / "stylesheets" / "new.css").exists())
            self.assertFalse((tmp_path / "stylesheets" / "old.css").exists())
            manifest = json.loads((tmp_path / "manifest.json").read_text())
            self.assertNotIn("stylesheets/old.css", manifest)
            self.assertIn("stylesheets/new.css", manifest)
            self.assertEqual(manifest["stylesheets/new.css"]["id"], 99)
            self.assertEqual(manifest["stylesheets/new.css"]["type"], "asset")
            # Schema parity with pull.py: kind, not asset_type — same key
            # any future manifest reader is most likely to use.
            self.assertEqual(manifest["stylesheets/new.css"]["kind"], "stylesheet")
            self.assertNotIn("asset_type", manifest["stylesheets/new.css"])

    def test_replace_with_path_separator_rejected(self):
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO):
            rc = layouts_cmd.cmd_asset_replace(
                _args(asset_id=5, new_filename="evil/path.css"), client
            )
        self.assertEqual(rc, 2)
        client.get.assert_not_called()


class TestLayoutCreate(unittest.TestCase):
    def test_create_layout_default_content_type(self):
        client = _make_client()
        client.post.return_value = {"id": 100, "updated_at": "2026-01-01"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            (tmp_path / "layouts" / "Hero.tpl").write_text("body", encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = layouts_cmd.cmd_layout_create(_args(args=["layouts/Hero.tpl"]), client)
            finally:
                os.chdir(cwd_before)

        self.assertEqual(rc, 0)
        client.post.assert_called_once()
        call = client.post.call_args
        self.assertEqual(call.args[0], "/layouts")
        payload = call.args[1]
        self.assertEqual(payload["title"], "Hero")
        self.assertEqual(payload["component"], False)
        self.assertEqual(payload["content_type"], "page")  # default

    def test_create_component_omits_content_type(self):
        client = _make_client()
        client.post.return_value = {"id": 100, "updated_at": ""}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "components").mkdir()
            (tmp_path / "components" / "Footer.tpl").write_text("body", encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = layouts_cmd.cmd_layout_create(
                        _args(args=["components/Footer.tpl"]), client
                    )
            finally:
                os.chdir(cwd_before)

        self.assertEqual(rc, 0)
        payload = client.post.call_args.args[1]
        self.assertEqual(payload["component"], True)
        self.assertNotIn("content_type", payload)

    def test_create_with_explicit_content_type(self):
        client = _make_client()
        client.post.return_value = {"id": 100, "updated_at": ""}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            (tmp_path / "layouts" / "Post.tpl").write_text("body", encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stdout", new_callable=io.StringIO):
                    layouts_cmd.cmd_layout_create(
                        _args(args=["--content-type=blog_article", "layouts/Post.tpl"]),
                        client,
                    )
            finally:
                os.chdir(cwd_before)
        self.assertEqual(client.post.call_args.args[1]["content_type"], "blog_article")

    def test_create_explicit_kind_mismatch_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            (tmp_path / "layouts" / "Hero.tpl").write_text("body", encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    with patch("sys.stdout", new_callable=io.StringIO):
                        rc = layouts_cmd.cmd_layout_create(
                            _args(args=["component", "layouts/Hero.tpl"]), client
                        )
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 2)
        self.assertIn("does not match path", stderr.getvalue())
        client.post.assert_not_called()

    def test_create_path_outside_components_or_layouts_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "other").mkdir()
            (tmp_path / "other" / "x.tpl").write_text("x", encoding="utf-8")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    rc = layouts_cmd.cmd_layout_create(_args(args=["other/x.tpl"]), client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 2)
        self.assertIn("under 'components/' or 'layouts/'", stderr.getvalue())

    def test_create_collision_with_existing_manifest_entry(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            (tmp_path / "layouts" / "Hero.tpl").write_text("body", encoding="utf-8")
            (tmp_path / "manifest.json").write_text(
                json.dumps({"layouts/Hero.tpl": {"id": 1, "type": "layout"}}),
                encoding="utf-8",
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    rc = layouts_cmd.cmd_layout_create(_args(args=["layouts/Hero.tpl"]), client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 1)
        self.assertIn("already exists in manifest", stderr.getvalue())
        client.post.assert_not_called()

    def test_create_missing_file_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "layouts").mkdir()
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    rc = layouts_cmd.cmd_layout_create(_args(args=["layouts/Missing.tpl"]), client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 1)
        self.assertIn("not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
