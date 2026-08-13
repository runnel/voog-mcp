"""Tests for voog.mcp.tools.assets (asset_upload — issue #140 item 3)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._test_helpers import _ann_get
from voog.mcp.tools import assets as assets_tools


def _jpeg(tmpdir: str, name: str = "photo.jpg") -> str:
    path = Path(tmpdir) / name
    path.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    return str(path)


class TestSchema(unittest.TestCase):
    def test_one_tool_named_asset_upload(self):
        self.assertEqual([t.name for t in assets_tools.get_tools()], ["asset_upload"])

    def test_requires_site_and_files(self):
        schema = assets_tools.get_tools()[0].inputSchema
        self.assertEqual(sorted(schema["required"]), ["files", "site"])

    def test_annotations_explicit(self):
        ann = assets_tools.get_tools()[0].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_destructive_hint_set_because_uploads_publish(self):
        # The tool reads an arbitrary local path and publishes it at a public
        # URL. SECURITY.md leans on destructiveHint for the host's approval
        # prompt, and product_set_images already carries it — without this,
        # these were the only arbitrary-file-to-public-web tools a compliant
        # host would not prompt on.
        ann = assets_tools.get_tools()[0].annotations
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), True)

    def test_schema_defaults_match_handler_defaults(self):
        # Drift guard: a schema default that disagrees with the handler
        # makes the tool behave differently than its description promises.
        props = assets_tools.get_tools()[0].inputSchema["properties"]
        self.assertIs(props["allow_duplicate"]["default"], False)
        self.assertIs(props["wait_for_sizes"]["default"], True)


class TestValidation(unittest.TestCase):
    def _err(self, args):
        client = MagicMock()
        result = assets_tools.call_tool("asset_upload", args, client)
        self.assertTrue(result.isError)
        return json.loads(result.content[0].text)["error"], client

    def test_files_must_be_non_empty(self):
        err, client = self._err({"files": []})
        self.assertIn("non-empty", err)
        client.post.assert_not_called()

    def test_relative_path_rejected(self):
        err, _ = self._err({"files": ["photos/x.jpg"]})
        self.assertIn("absolute", err)

    def test_missing_file_rejected(self):
        err, _ = self._err({"files": ["/nonexistent/really/x.jpg"]})
        self.assertIn("does not exist", err)

    def test_unsupported_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _jpeg(tmp, "notes.txt")
            err, _ = self._err({"files": [path]})
        self.assertIn("unsupported type", err)

    def test_bad_path_aborts_before_any_upload(self):
        # Pre-flight is all-or-nothing on purpose: a bad path discovered
        # halfway through would leave a partial upload set in the library.
        with tempfile.TemporaryDirectory() as tmp:
            good = _jpeg(tmp, "good.jpg")
            client = MagicMock()
            result = assets_tools.call_tool(
                "asset_upload", {"files": [good, "/nope/bad.jpg"]}, client
            )
        self.assertTrue(result.isError)
        client.post.assert_not_called()


class TestUploadBehaviour(unittest.TestCase):
    def _client(self):
        client = MagicMock()
        client.get.return_value = []  # no existing asset by that name
        return client

    def test_reuses_existing_asset_by_default(self):
        # Voog auto-suffixes a duplicate filename rather than overwriting,
        # so a blind re-upload orphans the original and silently changes
        # which file a reference points at.
        client = self._client()
        existing = {
            "id": 77,
            "filename": "photo.jpg",
            "status": "done",
            "width": 150,
            "height": 200,
            "sizes": [{"width": 113, "height": 150, "filename": "photo_medium.webp"}],
        }
        client.get.return_value = [existing]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(assets_tools, "_upload_asset") as upload:
                result = assets_tools.call_tool("asset_upload", {"files": [_jpeg(tmp)]}, client)
        upload.assert_not_called()
        payload = json.loads(result[1].text)
        self.assertEqual(payload["uploaded"], [])
        self.assertEqual(payload["reused"][0]["id"], 77)
        self.assertEqual(payload["reused"][0]["path"], "/photos/photo.jpg")

    def test_allow_duplicate_forces_a_fresh_upload(self):
        client = self._client()
        client.get.return_value = [{"id": 77, "filename": "photo.jpg", "status": "done"}]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(assets_tools, "_upload_asset") as upload:
                upload.return_value = {"id": 78, "width": 10, "height": 10}
                assets_tools.call_tool(
                    "asset_upload",
                    {"files": [_jpeg(tmp)], "allow_duplicate": True, "wait_for_sizes": False},
                    client,
                )
        upload.assert_called_once()

    def test_returns_derivative_widths_for_srcset(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(assets_tools, "_upload_asset") as upload:
                upload.return_value = {"id": 91, "width": 1800, "height": 2400}
                client.get.side_effect = [
                    [],  # find_asset_by_filename
                    {  # wait_for_derivatives
                        "id": 91,
                        "filename": "photo.jpg",
                        "height": 2400,
                        "width": 1800,
                        "sizes": [
                            {"width": w, "height": h, "filename": f"photo_{n}.webp"}
                            for w, h, n in (
                                (1536, 2048, "huge"),
                                (960, 1280, "large"),
                                (450, 600, "block"),
                                (113, 150, "medium"),
                            )
                        ],
                    },
                ]
                result = assets_tools.call_tool("asset_upload", {"files": [_jpeg(tmp)]}, client)
        payload = json.loads(result[1].text)
        widths = [s["width"] for s in payload["uploaded"][0]["sizes"]]
        self.assertEqual(widths, [113, 450, 960, 1536])

    def test_wait_for_sizes_false_skips_polling(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(assets_tools, "_upload_asset") as upload:
                upload.return_value = {"id": 92, "width": 10, "height": 10}
                assets_tools.call_tool(
                    "asset_upload",
                    {"files": [_jpeg(tmp)], "wait_for_sizes": False},
                    client,
                )
        # Only the reuse lookup hit the API — no /assets/{id} poll.
        self.assertTrue(all("q.asset.filename" in c.args[0] for c in client.get.call_args_list))

    def test_per_file_failure_does_not_abort_the_rest(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            first, second = _jpeg(tmp, "a.jpg"), _jpeg(tmp, "b.jpg")
            with patch.object(assets_tools, "_upload_asset") as upload:
                upload.side_effect = [
                    RuntimeError("S3 upload failed: HTTP 500"),
                    {"id": 2, "width": 10, "height": 10},
                ]
                result = assets_tools.call_tool(
                    "asset_upload",
                    {"files": [first, second], "wait_for_sizes": False},
                    client,
                )
        payload = json.loads(result[1].text)
        self.assertEqual(len(payload["failed"]), 1)
        self.assertIn("HTTP 500", payload["failed"][0]["error"])
        self.assertEqual(payload["uploaded"][0]["id"], 2)


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_errors(self):
        result = assets_tools.call_tool("asset_delete", {}, MagicMock())
        self.assertTrue(result.isError)


if __name__ == "__main__":
    unittest.main()
