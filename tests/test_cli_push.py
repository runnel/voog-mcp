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


class TestPushLegacyLayoutAssetType(unittest.TestCase):
    """Manifests created by the pre-rename `voog.py` script wrote
    ``"type": "layout_asset"`` for CSS/JS entries; current `voog pull`
    writes ``"type": "asset"``. Push must accept both — issue #96 was
    actually a silent no-op against legacy manifests, where neither
    branch of the old if/elif matched and the PUT was never sent. The
    fixed code in #98 + #101 converted the silent no-op into a hard
    `unknown manifest type` error, which is correct but breaks legacy
    manifests in place. Treat the legacy spelling as an alias instead.
    """

    def test_legacy_layout_asset_type_routes_to_layout_assets_endpoint(self):
        client = _make_client()
        client.put.return_value = {"id": 5, "size": 19}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/cart.css": {
                        "id": 5,
                        "type": "layout_asset",  # legacy voog.py spelling
                        "updated_at": "",
                    },
                },
                files={"stylesheets/cart.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["stylesheets/cart.css"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/layout_assets/5", {"data": "body { margin: 0; }"})

    def test_legacy_layout_asset_type_runs_size_verification(self):
        # Legacy spelling must get the same silent-no-op detection that
        # modern "asset" entries get — otherwise this PR re-opens the
        # bug it's trying to fix.
        client = _make_client()
        client.put.return_value = {"id": 5, "size": 999}  # mismatch
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/cart.css": {
                        "id": 5,
                        "type": "layout_asset",
                        "updated_at": "",
                    },
                },
                files={"stylesheets/cart.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    args = MagicMock()
                    args.files = ["stylesheets/cart.css"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)
        self.assertIn("size", stderr.getvalue())


class TestPushManifestSelfHeal(unittest.TestCase):
    """Successful push of a legacy `"layout_asset"` entry should normalize
    the manifest's `type` field to `"asset"` so the manifest gradually
    self-heals instead of staying legacy forever."""

    def test_legacy_type_normalized_to_asset_on_successful_push(self):
        client = _make_client()
        client.put.return_value = {"id": 5, "size": 19, "updated_at": "2026-05-01T11:00:00.000Z"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/cart.css": {
                        "id": 5,
                        "type": "layout_asset",
                        "updated_at": "",
                    },
                },
                files={"stylesheets/cart.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["stylesheets/cart.css"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
            updated = json.loads((tmp_path / "manifest.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(updated["stylesheets/cart.css"]["type"], "asset")

    def test_legacy_type_not_normalized_on_failed_push(self):
        # If the verification fails, the manifest writeback is skipped
        # entirely (existing semantic from #101) — type stays legacy.
        client = _make_client()
        client.put.return_value = {"id": 5, "size": 999}  # mismatch
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            initial = {
                "stylesheets/cart.css": {
                    "id": 5,
                    "type": "layout_asset",
                    "updated_at": "",
                }
            }
            _setup_pulled_tree(
                tmp_path,
                manifest=initial,
                files={"stylesheets/cart.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO):
                    args = MagicMock()
                    args.files = ["stylesheets/cart.css"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
            after = json.loads((tmp_path / "manifest.json").read_text())
        self.assertNotEqual(rc, 0)
        self.assertEqual(after, initial)


class TestPushMixedManifestTypes(unittest.TestCase):
    """A real-world checkout may have both legacy `"layout_asset"` and
    modern `"asset"` entries side by side mid-migration. Push must
    handle both in the same multi-file invocation without ordering or
    state-leak issues."""

    def test_modern_and_legacy_assets_in_same_push_both_succeed(self):
        client = _make_client()
        client.put.side_effect = [
            {"id": 5, "size": 19, "updated_at": "2026-05-01T11:00:00.000Z"},  # legacy
            {"id": 7, "size": 19, "updated_at": "2026-05-01T11:00:01.000Z"},  # modern
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/cart.css": {
                        "id": 5,
                        "type": "layout_asset",  # legacy
                        "updated_at": "",
                    },
                    "stylesheets/main.css": {
                        "id": 7,
                        "type": "asset",  # modern
                        "updated_at": "",
                    },
                },
                files={
                    "stylesheets/cart.css": "body { margin: 0; }",
                    "stylesheets/main.css": "body { margin: 0; }",
                },
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                args = MagicMock()
                args.files = ["stylesheets/cart.css", "stylesheets/main.css"]
                rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
            updated = json.loads((tmp_path / "manifest.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(client.put.call_count, 2)
        # Both routed to /layout_assets, both legacy normalized.
        client.put.assert_any_call("/layout_assets/5", {"data": "body { margin: 0; }"})
        client.put.assert_any_call("/layout_assets/7", {"data": "body { margin: 0; }"})
        self.assertEqual(updated["stylesheets/cart.css"]["type"], "asset")
        self.assertEqual(updated["stylesheets/main.css"]["type"], "asset")


class TestPushPartialFailure(unittest.TestCase):
    """A failing PUT in the middle of a multi-file push must not block
    subsequent files. Final exit code reflects whether anything failed."""

    def test_one_failing_push_does_not_block_remaining_files(self):
        client = _make_client()
        client.put.side_effect = [
            {"id": 5, "size": 999},  # asset: size mismatch → fail
            {"id": 1, "updated_at": "2026-05-01T11:22:33.000Z"},  # layout: ok
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "stylesheets/main.css": {"id": 5, "type": "asset", "updated_at": ""},
                    "layouts/Front.tpl": {
                        "id": 1,
                        "type": "layout",
                        "updated_at": "2026-04-30T10:00:00.000Z",
                    },
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


class TestPushUpdatedAtMixedPrecision(unittest.TestCase):
    """ISO 8601 string comparison breaks when fractional-second precision
    differs between the manifest and the response. Push must parse
    timestamps before comparing."""

    def test_response_no_millis_manifest_with_millis_advance_recognized(self):
        # Manifest stored "...:00.000Z"; response says "...:01Z" (later
        # but no fractional seconds). String compare would mis-rank these
        # because '.' < 'Z' lexically. datetime parse handles it.
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "2026-04-30T10:00:01Z"}
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

    def test_unparseable_timestamp_falls_through(self):
        # Garbage in either field → no signal → don't false-positive.
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "not-a-date"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _setup_pulled_tree(
                tmp_path,
                manifest={
                    "layouts/Front.tpl": {
                        "id": 1,
                        "type": "layout",
                        "updated_at": "also-garbage",
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

    def test_response_older_than_manifest_fails_loudly(self):
        # Pin the <= behaviour: server timestamp older than manifest is
        # also a "didn't advance" failure (e.g. clock skew, cached read).
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "2026-04-29T10:00:00.000Z"}
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
                with patch("sys.stderr", new_callable=io.StringIO):
                    args = MagicMock()
                    args.files = ["layouts/Front.tpl"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
        self.assertNotEqual(rc, 0)


class TestPushManifestRefresh(unittest.TestCase):
    """After a successful push, the manifest's `updated_at` should advance
    to the response's value so a second push without an intervening pull
    has a fresh anchor for the layout verification check."""

    def test_successful_push_writes_back_updated_at_to_manifest(self):
        client = _make_client()
        client.put.return_value = {"id": 1, "updated_at": "2026-05-01T11:22:33.000Z"}
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
            updated_manifest = json.loads((tmp_path / "manifest.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(
            updated_manifest["layouts/Front.tpl"]["updated_at"],
            "2026-05-01T11:22:33.000Z",
        )

    def test_failed_push_does_not_dirty_manifest(self):
        client = _make_client()
        client.put.return_value = {"id": 5, "size": 999}  # mismatch
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            initial_manifest = {
                "stylesheets/main.css": {
                    "id": 5,
                    "type": "asset",
                    "updated_at": "2026-04-30T10:00:00.000Z",
                },
            }
            _setup_pulled_tree(
                tmp_path,
                manifest=initial_manifest,
                files={"stylesheets/main.css": "body { margin: 0; }"},
            )
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp_path)
                with patch("sys.stderr", new_callable=io.StringIO):
                    args = MagicMock()
                    args.files = ["stylesheets/main.css"]
                    rc = push_cmd.run(args, client)
            finally:
                os.chdir(cwd_before)
            after = json.loads((tmp_path / "manifest.json").read_text())
        self.assertNotEqual(rc, 0)
        self.assertEqual(after, initial_manifest)


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
