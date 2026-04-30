"""Tests for voog.cli.commands.push — manifest-driven file upload.

The push command reads ``manifest.json`` to discover what to send and
where (layouts vs layout_assets), then PUTs the file body to the right
endpoint. Tests cover the manifest-missing error, file-not-in-manifest
skip behavior, and the bulk-confirmation prompt.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import push as push_cmd


def _make_client():
    client = MagicMock()
    client.host = "example.com"
    return client


def _setup_pulled_tree(tmp: Path, manifest: dict, files: dict[str, str]) -> None:
    """Create a directory tree as if `voog pull` had run."""
    tmp.mkdir(exist_ok=True)
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    (tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class TestPushManifestRequired(unittest.TestCase):
    def test_missing_manifest_returns_error(self):
        # Without manifest.json the command must abort with a clear
        # error pointing the user at `voog pull`.
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    args = MagicMock()
                    args.files = []
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 1)
        self.assertIn("manifest.json missing", stderr.getvalue())
        self.assertIn("voog pull", stderr.getvalue())
        client.put.assert_not_called()


class TestPushSpecificFiles(unittest.TestCase):
    def test_named_files_pushed_to_layouts_endpoint(self):
        client = _make_client()
        client.put.return_value = {"id": 1}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                files={"layouts/Front.tpl": "{% layout %}"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["layouts/Front.tpl"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/layouts/1", {"layout": {"body": "{% layout %}"}})

    def test_layout_asset_files_pushed_to_layout_assets_endpoint(self):
        client = _make_client()
        client.put.return_value = {"id": 5}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {
                        "id": 5,
                        "type": "asset",
                        "updated_at": "",
                    },
                },
                files={"stylesheets/main.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["stylesheets/main.css"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with(
            "/layout_assets/5", {"layout_asset": {"data": "body { margin: 0; }"}}
        )

    def test_file_not_in_manifest_is_skipped_with_warning(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                files={"layouts/Front.tpl": "x"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    args = MagicMock()
                    args.files = ["layouts/Unknown.tpl"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        # Skipping unknown file does NOT abort — others continue.
        # Here we passed only the unknown file, so put is never called.
        self.assertEqual(rc, 0)
        self.assertIn("not in manifest", stderr.getvalue())
        client.put.assert_not_called()


class TestPushAllConfirmation(unittest.TestCase):
    def test_no_files_arg_prompts_for_confirmation(self):
        client = _make_client()
        client.put.return_value = {"id": 1}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                files={"layouts/Front.tpl": "x"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("builtins.input", return_value="y"):
                    args = MagicMock()
                    args.files = []
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 0)
        client.put.assert_called_once()

    def test_no_files_arg_aborts_when_user_says_n(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                files={"layouts/Front.tpl": "x"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("builtins.input", return_value="n"):
                    args = MagicMock()
                    args.files = []
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        # Aborted = clean exit, no PUTs.
        self.assertEqual(rc, 0)
        client.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
