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
        # Voog's /layouts PUT takes a flat payload — wrapping in
        # {"layout": {...}} happens to be tolerated for layouts but is
        # inconsistent with the documented convention and with MCP
        # _layout_update. Send flat to keep CLI and MCP aligned.
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
        client.put.assert_called_once_with("/layouts/1", {"body": "{% layout %}"})

    def test_layout_asset_files_pushed_to_layout_assets_endpoint(self):
        # Regression test for issue #96: wrapping the layout_assets PUT in
        # {"layout_asset": {...}} is silently 200-ed by Voog without
        # persisting. The flat form is the only one that actually updates
        # the asset content.
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
        client.put.assert_called_once_with("/layout_assets/5", {"data": "body { margin: 0; }"})

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


class TestPushSilentNoOpDetection(unittest.TestCase):
    """Issue #96: Voog returned 200 with empty stored content for the
    wrapped-form bug. Push must surface this rather than print ✓."""

    def test_layout_asset_response_with_empty_data_fails_loudly(self):
        client = _make_client()
        # Simulate Voog's silent-no-op response shape: 200 + the resource
        # echoed back with the body field cleared.
        client.put.return_value = {"id": 5, "data": ""}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
                },
                files={"stylesheets/main.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    patch("sys.stdout", new_callable=io.StringIO) as stdout,
                ):
                    args = MagicMock()
                    args.files = ["stylesheets/main.css"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0, "push must exit non-zero on silent no-op")
        self.assertIn("NOT updated", stderr.getvalue())
        self.assertNotIn("✓ stylesheets/main.css", stdout.getvalue())

    def test_layout_response_with_empty_body_fails_loudly(self):
        client = _make_client()
        client.put.return_value = {"id": 1, "body": ""}
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
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    patch("sys.stdout", new_callable=io.StringIO) as stdout,
                ):
                    args = MagicMock()
                    args.files = ["layouts/Front.tpl"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)
        self.assertIn("NOT updated", stderr.getvalue())
        self.assertNotIn("✓ layouts/Front.tpl", stdout.getvalue())

    def test_response_echoing_content_back_is_treated_as_success(self):
        # The healthy case: server echoes the resource with the same body.
        client = _make_client()
        client.put.return_value = {"id": 5, "data": "body { margin: 0; }"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
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

    def test_response_omitting_content_field_is_tolerated(self):
        # Some endpoints / versions may return a slim response without the
        # content field. We can't verify in that case — don't false-positive.
        client = _make_client()
        client.put.return_value = {"id": 5}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
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

    def test_empty_local_file_skips_no_op_check(self):
        # Pushing an empty file legitimately produces an empty stored
        # body — that's not a silent no-op, that's the user's intent.
        client = _make_client()
        client.put.return_value = {"id": 5, "data": ""}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
                },
                files={"stylesheets/main.css": ""},
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

    def test_one_failing_push_does_not_block_remaining_files(self):
        # A failing layout_asset must not stop the layout PUT that follows.
        # Both are attempted; final exit code reflects the failure.
        client = _make_client()
        client.put.side_effect = [
            {"id": 5, "data": ""},  # asset: silent no-op
            {"id": 1, "body": "{% layout %}"},  # layout: succeeds
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
                    "layouts/Front.tpl": {"id": 1, "type": "layout", "updated_at": ""},
                },
                files={
                    "stylesheets/main.css": "body { margin: 0; }",
                    "layouts/Front.tpl": "{% layout %}",
                },
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO):
                    args = MagicMock()
                    args.files = ["stylesheets/main.css", "layouts/Front.tpl"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)
        self.assertEqual(client.put.call_count, 2)


class TestPushResponseSizeVerification(unittest.TestCase):
    """Voog's PUT /layout_assets response is slim — it omits `data`, but
    DOES include `size`. Empirical follow-up to #96: post-merge
    verification showed the original detector never trips because the
    `data` field is never echoed back. The `size` field is the reliable
    signal: if stored size != local body size, content was not persisted.
    """

    def test_asset_size_mismatch_fails_loudly(self):
        client = _make_client()
        # Server says it stored 999 bytes; we sent 19. Hard fail.
        client.put.return_value = {"id": 5, "size": 999}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
                },
                files={"stylesheets/main.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    patch("sys.stdout", new_callable=io.StringIO) as stdout,
                ):
                    args = MagicMock()
                    args.files = ["stylesheets/main.css"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)
        self.assertIn("size", stderr.getvalue())
        self.assertIn("NOT updated", stderr.getvalue())
        self.assertNotIn("✓ stylesheets/main.css", stdout.getvalue())

    def test_asset_size_match_succeeds(self):
        client = _make_client()
        # Healthy slim response: id + matching size.
        client.put.return_value = {"id": 5, "size": 19}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
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

    def test_asset_size_field_omitted_is_tolerated(self):
        # Some responses might omit `size` (older endpoints, errors, etc.).
        # Don't false-positive — fall through to the existing slim path.
        client = _make_client()
        client.put.return_value = {"id": 5}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
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

    def test_asset_size_uses_byte_count_not_char_count(self):
        # Multi-byte UTF-8 (ä, õ, …) — Voog reports byte count, not char.
        client = _make_client()
        body = "/* käömnõ */"  # 12 chars, but ö/õ are 2 bytes each
        expected_bytes = len(body.encode("utf-8"))
        self.assertNotEqual(len(body), expected_bytes, "test premise: utf-8 expansion")
        client.put.return_value = {"id": 5, "size": expected_bytes}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/utf.css": {"id": 5, "type": "asset", "updated_at": ""},
                },
                files={"stylesheets/utf.css": body},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["stylesheets/utf.css"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 0)


class TestPushLayoutUpdatedAtVerification(unittest.TestCase):
    """Voog's PUT /layouts response also omits the body but includes
    `updated_at`. If the response's updated_at didn't advance from the
    manifest's stored value, the layout content didn't change."""

    def test_layout_updated_at_unchanged_fails_loudly(self):
        client = _make_client()
        # Response timestamp == manifest's stored timestamp → no advance.
        client.put.return_value = {"id": 1, "updated_at": "2026-04-30T10:00:00.000Z"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {
                        "id": 1,
                        "type": "layout",
                        "updated_at": "2026-04-30T10:00:00.000Z",
                    },
                },
                files={"layouts/Front.tpl": "{% layout %}"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with (
                    patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    patch("sys.stdout", new_callable=io.StringIO) as stdout,
                ):
                    args = MagicMock()
                    args.files = ["layouts/Front.tpl"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)
        self.assertIn("updated_at", stderr.getvalue())
        self.assertNotIn("✓ layouts/Front.tpl", stdout.getvalue())

    def test_layout_updated_at_advanced_succeeds(self):
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "2026-05-01T10:00:00.000Z"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {
                        "id": 1,
                        "type": "layout",
                        "updated_at": "2026-04-30T10:00:00.000Z",
                    },
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

    def test_layout_no_manifest_updated_at_skips_check(self):
        # Hand-crafted manifests / older pulls may have empty updated_at.
        # Skip the timestamp check rather than false-positive.
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "2026-05-01T10:00:00.000Z"}
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
