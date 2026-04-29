"""Tests for voog_mcp.tools.products_images.

Covers the 3-step Voog asset upload protocol via mocks:

    1. POST /assets (admin/api) → {id, upload_url}
    2. PUT upload_url with raw binary body (urllib.request, NOT VoogClient)
    3. PUT /assets/{id}/confirm (admin/api) → {public_url, width, height}

Then a final PUT /products/{id} (ecommerce_url) with flat
{image_id, asset_ids} payload.

Mutating + creates new asset records — never run against live example.com.
"""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._test_helpers import _ann_get
from voog.mcp.tools import products_images as products_images_tools


def _make_client():
    """Fake VoogClient with both base URLs set, like other tests in this repo."""
    client = MagicMock()
    client.host = "test.example.com"
    client.base_url = "https://test.example.com/admin/api"
    client.ecommerce_url = "https://test.example.com/admin/api/ecommerce/v1"
    return client


def _write_image(dirpath: Path, name: str, body: bytes = b"\x89PNG\r\n\x1a\nfake") -> Path:
    """Write a fake image file (header bytes are arbitrary — extension is what matters)."""
    p = dirpath / name
    p.write_bytes(body)
    return p


class TestGetTools(unittest.TestCase):
    def test_get_tools_returns_one(self):
        tools = products_images_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["product_set_images"])

    def test_schema_shape(self):
        tools = {t.name: t for t in products_images_tools.get_tools()}
        schema = tools["product_set_images"].inputSchema
        self.assertEqual(schema["properties"]["product_id"]["type"], "integer")
        self.assertEqual(schema["properties"]["files"]["type"], "array")
        self.assertEqual(schema["properties"]["files"]["minItems"], 1)
        self.assertEqual(schema["properties"]["force"]["type"], "boolean")
        for req in ("product_id", "files"):
            self.assertIn(req, schema["required"])
        self.assertNotIn("force", schema["required"])

    def test_full_explicit_annotation_triple(self):
        # readOnlyHint=False (mutates), destructiveHint=True (replaces existing
        # images — old asset_ids are unlinked from product), idempotentHint=False
        # (each call uploads new asset records with different ids).
        tools = {t.name: t for t in products_images_tools.get_tools()}
        ann = tools["product_set_images"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), True)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), False)


class TestValidation(unittest.TestCase):
    """Pre-API validation — no client calls should be made when invalid."""

    def test_missing_product_id_rejected(self):
        client = _make_client()
        result = products_images_tools.call_tool(
            "product_set_images",
            {"files": ["/tmp/x.jpg"]},
            client,
        )
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("product_id", payload["error"])

    def test_empty_files_rejected(self):
        client = _make_client()
        result = products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": []},
            client,
        )
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_relative_path_rejected(self):
        client = _make_client()
        result = products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": ["relative/path.jpg"]},
            client,
        )
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("absolute", payload["error"])

    def test_nonexistent_file_rejected(self):
        client = _make_client()
        result = products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": ["/nonexistent/path/img.jpg"]},
            client,
        )
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_unsupported_extension_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            txt = _write_image(tmpdir, "doc.txt", b"hello")
            result = products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(txt)]},
                client,
            )
            client.post.assert_not_called()
            client.put.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn(".txt", payload["error"])

    def test_pdf_extension_rejected(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            pdf = _write_image(tmpdir, "x.pdf", b"%PDF")
            products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(pdf)]},
                client,
            )
            client.post.assert_not_called()
            client.put.assert_not_called()


class TestForceGuard(unittest.TestCase):
    """Defensive opt-in like page_delete: refuses to replace existing images
    unless force=true."""

    def test_force_false_blocks_when_product_has_existing_images(self):
        client = _make_client()
        client.get.return_value = {
            "id": 42,
            "name": "Widget",
            "asset_ids": [100, 101],
        }
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "new.jpg")
            result = products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(img)]},
                client,
            )
            client.post.assert_not_called()
            client.put.assert_not_called()
            self.assertTrue(result.isError)
            payload = json.loads(result.content[0].text)
            self.assertIn("error", payload)
            self.assertIn("force", payload["error"])

    def test_force_false_allowed_when_product_has_no_existing_images(self):
        # Empty asset_ids — replacing nothing — force=false is safe
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        client.post.return_value = {"id": 200, "upload_url": "https://s3.example.com/up200"}
        client.put.side_effect = [
            {
                "id": 200,
                "public_url": "https://cdn/200.jpg",
                "width": 800,
                "height": 600,
            },  # confirm
            {"id": 42, "asset_ids": [200], "image_id": 200},  # final product PUT
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "new.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )
            payload = json.loads(result[-1].text)
            self.assertEqual(payload["new_asset_ids"], [200])

    def test_force_true_proceeds_with_existing_images(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": [99]}
        client.post.return_value = {"id": 200, "upload_url": "https://s3.example.com/up200"}
        client.put.side_effect = [
            {"id": 200, "public_url": "https://cdn/200.jpg", "width": 800, "height": 600},
            {"id": 42, "asset_ids": [200], "image_id": 200},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "new.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)], "force": True},
                    client,
                )
            payload = json.loads(result[-1].text)
            self.assertEqual(payload["old_asset_ids"], [99])
            self.assertEqual(payload["new_asset_ids"], [200])


class TestSuccessPath(unittest.TestCase):
    """All uploads succeed → final PUT goes through with correct asset_ids."""

    def test_three_step_upload_per_file_then_product_put(self):
        # Filename-keyed dispatch — uploads now run in parallel, so call
        # ordering on client.post / client.put is non-deterministic. Pin
        # asset_id to filename so the assertions don't race.
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}

        post_ids = {"main.jpg": 201, "gallery.png": 202}

        def post_dispatch(path, body, **kwargs):
            aid = post_ids[body["filename"]]
            return {"id": aid, "upload_url": f"https://s3.example.com/up{aid}"}

        client.post.side_effect = post_dispatch

        # PUT handler covers both per-asset confirms (2x) and the final
        # product PUT (1x). The product PUT is the only path-keyed match
        # that isn't an /assets/N/confirm.
        product_put_payload = None

        def put_dispatch(path, body=None, **kwargs):
            nonlocal product_put_payload
            if path.endswith("/confirm"):
                aid = int(path.split("/")[2])
                return {
                    "id": aid,
                    "public_url": f"https://cdn/{aid}.jpg",
                    "width": 1200,
                    "height": 800,
                }
            # /products/42
            product_put_payload = body
            return {
                "id": 42,
                "asset_ids": list((body or {}).get("asset_ids", [])),
                "image_id": (body or {}).get("image_id"),
            }

        client.put.side_effect = put_dispatch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "main.jpg")
            img2 = _write_image(tmpdir, "gallery.png")

            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img1), str(img2)]},
                    client,
                )

        # POST /assets called once per file with admin/api default base —
        # check by aggregating call args, not by index (parallel uploads).
        self.assertEqual(client.post.call_count, 2)
        post_paths = [c.args[0] for c in client.post.call_args_list]
        self.assertEqual(set(post_paths), {"/assets"})
        post_bodies = {c.args[1]["filename"]: c.args[1] for c in client.post.call_args_list}
        self.assertEqual(post_bodies["main.jpg"]["content_type"], "image/jpeg")
        self.assertEqual(post_bodies["main.jpg"]["size"], len(b"\x89PNG\r\n\x1a\nfake"))
        self.assertEqual(post_bodies["gallery.png"]["content_type"], "image/png")

        # urlopen called once per file (binary PUT to upload_url)
        self.assertEqual(mock_urlopen.call_count, 2)

        # PUT calls: 2× confirm + 1× product update — assert as a multiset
        # since confirm completion order is non-deterministic.
        self.assertEqual(client.put.call_count, 3)
        put_paths = [c.args[0] for c in client.put.call_args_list]
        self.assertEqual(
            sorted(put_paths),
            sorted(["/assets/201/confirm", "/assets/202/confirm", "/products/42"]),
        )

        # Final product PUT — flat payload {image_id, asset_ids} on
        # ecommerce_url (NOT wrapped in {product: {...}} — different from
        # product_update, voog.py CLI confirms this shape). uploaded order
        # mirrors input order (parallel_map preserves it), so main.jpg is
        # always first → asset_id 201 is the image_id.
        product_put_call = next(
            c for c in client.put.call_args_list if c.args and c.args[0] == "/products/42"
        )
        self.assertEqual(
            product_put_call.args[1],
            {"image_id": 201, "asset_ids": [201, 202]},
        )
        self.assertEqual(
            product_put_call.kwargs["base"],
            "https://test.example.com/admin/api/ecommerce/v1",
        )

        payload = json.loads(result[-1].text)
        self.assertEqual(payload["product_id"], 42)
        self.assertEqual(payload["new_asset_ids"], [201, 202])
        self.assertEqual(payload["old_asset_ids"], [])
        self.assertEqual(len(payload["uploaded"]), 2)
        # Input order is preserved by parallel_map, so uploaded[0] is main.jpg.
        self.assertEqual(payload["uploaded"][0]["filename"], "main.jpg")
        self.assertEqual(payload["uploaded"][0]["asset_id"], 201)
        self.assertEqual(payload["uploaded"][1]["filename"], "gallery.png")
        self.assertEqual(payload["uploaded"][1]["asset_id"], 202)
        self.assertEqual(payload["failed"], [])

    def test_binary_upload_uses_correct_headers(self):
        """The PUT to upload_url must include Content-Type + x-amz-acl headers
        per voog.py."""
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        client.post.return_value = {"id": 201, "upload_url": "https://s3/up"}
        client.put.side_effect = [
            {"id": 201, "public_url": "u", "width": 100, "height": 100},
            {"id": 42, "asset_ids": [201], "image_id": 201},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.webp", b"webp-bytes")
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.urllib.request.Request") as mock_request,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )
            req_call = mock_request.call_args
            self.assertEqual(req_call.args[0], "https://s3/up")
            self.assertEqual(req_call.kwargs["data"], b"webp-bytes")
            self.assertEqual(req_call.kwargs["method"], "PUT")
            headers = req_call.kwargs.get("headers", {})
            self.assertEqual(headers.get("Content-Type"), "image/webp")
            self.assertEqual(headers.get("x-amz-acl"), "public-read")


class TestPartialFailure(unittest.TestCase):
    """If any single upload fails, the product is NOT updated.

    Rationale: a partial update would leave the product with a half-set of
    images. Better to surface the failure cleanly and let the caller retry.
    Successful uploads are still surfaced in `uploaded` so the caller can
    re-link them manually if desired.

    Under collect-then-decide (spec § 4.6) this invariant survives — what
    changes is orphan count: up to N-1 successful uploads can be left in
    Voog's library if any single upload fails (vs. 0..N-1 under the old
    first-failure-break loop, where subsequent uploads were skipped).
    """

    def test_any_upload_failure_prevents_product_put(self):
        """The 'any failure → no product PUT' invariant survives the move
        from first-failure-break to collect-then-decide. Replaces an earlier
        test that tacitly locked first-failure-stops-loop semantics by using
        ordered ``side_effect`` lists (which would race under parallel
        execution). Uses a filename-keyed side_effect so the success/failure
        mapping is deterministic regardless of upload thread interleaving.
        """
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}

        # Filename-keyed dispatch: the POST body carries `filename`, so we
        # can deterministically pick success vs. error per file no matter
        # which thread calls client.post first.
        def post_dispatch(path, body, **kwargs):
            if body["filename"] == "ok.jpg":
                return {"id": 201, "upload_url": "https://s3/up201"}
            raise urllib.error.HTTPError("url", 500, "Server Error", {}, None)

        client.post.side_effect = post_dispatch
        # Confirm of any successful upload returns benign data; product PUT
        # would consume the next side_effect entry but should never run.
        client.put.return_value = {
            "id": 201,
            "public_url": "u",
            "width": 1,
            "height": 1,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "ok.jpg")
            img2 = _write_image(tmpdir, "fails.jpg")

            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img1), str(img2)]},
                    client,
                )

        # The invariant under test: PUT /products/{id} must NOT have been
        # called when any upload failed.
        product_put_calls = [
            c for c in client.put.call_args_list if c.args and c.args[0] == "/products/42"
        ]
        self.assertEqual(product_put_calls, [])

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        details = payload["details"]
        # Both files were attempted (collect-then-decide), exactly one
        # succeeded and one failed. Orphan asset 201 is surfaced for the
        # caller to recover.
        self.assertEqual(len(details["uploaded"]), 1)
        self.assertEqual(details["uploaded"][0]["filename"], "ok.jpg")
        self.assertEqual(details["uploaded"][0]["asset_id"], 201)
        self.assertEqual(len(details["failed"]), 1)
        self.assertEqual(details["failed"][0]["filename"], "fails.jpg")

    def test_two_of_four_uploads_fail_no_product_put(self):
        """Collect-then-decide shape: with 4 uploads where 2 fail, all 4
        upload attempts run (no first-failure abort), 2 orphans land in
        ``uploaded``, 2 entries in ``failed``, and the product PUT never
        fires. Filename-keyed dispatch keeps the assertion deterministic
        under parallel execution.
        """
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}

        # 4 files: ok1, fail1, ok2, fail2. Success → asset id derived from
        # filename so the ``uploaded`` list is checkable independent of
        # upload completion order.
        success_ids = {"ok1.jpg": 301, "ok2.jpg": 302}

        def post_dispatch(path, body, **kwargs):
            name = body["filename"]
            if name in success_ids:
                aid = success_ids[name]
                return {"id": aid, "upload_url": f"https://s3/up{aid}"}
            raise urllib.error.HTTPError("url", 500, "boom", {}, None)

        client.post.side_effect = post_dispatch

        # Per-asset confirm response, keyed by URL path; product PUT (if it
        # ever ran, which it must not) would fall through to the default.
        def put_dispatch(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                aid = int(path.split("/")[2])
                return {"id": aid, "public_url": f"u{aid}", "width": 1, "height": 1}
            return {}

        client.put.side_effect = put_dispatch

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            files = [
                _write_image(tmpdir, "ok1.jpg"),
                _write_image(tmpdir, "fail1.jpg"),
                _write_image(tmpdir, "ok2.jpg"),
                _write_image(tmpdir, "fail2.jpg"),
            ]
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(p) for p in files]},
                    client,
                )

        # All 4 uploads were attempted (collect-then-decide, no abort)
        self.assertEqual(client.post.call_count, 4)
        # Product PUT must NOT have happened — any failure blocks it
        product_put_calls = [
            c for c in client.put.call_args_list if c.args and c.args[0] == "/products/42"
        ]
        self.assertEqual(product_put_calls, [])

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        details = payload["details"]
        self.assertEqual(len(details["uploaded"]), 2)
        self.assertEqual(len(details["failed"]), 2)
        uploaded_names = sorted(u["filename"] for u in details["uploaded"])
        self.assertEqual(uploaded_names, ["ok1.jpg", "ok2.jpg"])
        failed_names = sorted(f["filename"] for f in details["failed"])
        self.assertEqual(failed_names, ["fail1.jpg", "fail2.jpg"])
        uploaded_ids = sorted(u["asset_id"] for u in details["uploaded"])
        self.assertEqual(uploaded_ids, [301, 302])

    def test_failure_message_includes_orphan_recovery_guidance(self):
        """Spec § 4.6: under parallel collect-then-decide, orphan count can
        be up to N-1 (was 0..N-1 with first-failure-break). The error
        message MUST hand the caller a concrete next step rather than make
        them guess — the three documented recovery options must be present
        verbatim in the message text.
        """
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}

        def post_dispatch(path, body, **kwargs):
            if body["filename"] == "ok.jpg":
                return {"id": 201, "upload_url": "https://s3/up201"}
            raise urllib.error.HTTPError("url", 500, "boom", {}, None)

        client.post.side_effect = post_dispatch
        client.put.return_value = {
            "id": 201,
            "public_url": "u",
            "width": 1,
            "height": 1,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "ok.jpg")
            img2 = _write_image(tmpdir, "fails.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img1), str(img2)]},
                    client,
                )

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        msg = payload["error"]
        # Headline must include the "N of M" failure summary and product id
        self.assertIn("1 of 2 upload(s) failed", msg)
        self.assertIn("Product 42 NOT updated", msg)
        # All three recovery options must be spelled out — callers shouldn't
        # have to invent them.
        self.assertIn("Orphan asset_id(s)", msg)
        self.assertIn("Recovery options:", msg)
        self.assertIn("Re-run product_set_images", msg)
        self.assertIn("Manually link", msg)
        self.assertIn("DELETE /assets/{id}", msg)

    def test_s3_upload_failure_captured_per_file(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        client.post.return_value = {"id": 201, "upload_url": "https://s3/up"}

        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                # S3 returns 403 → upload failed
                mock_urlopen.return_value.__enter__.return_value.status = 403
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )

        # Confirm should not be called if S3 upload failed
        confirm_calls = [c for c in client.put.call_args_list if c.args and "confirm" in c.args[0]]
        self.assertEqual(confirm_calls, [])
        # Product PUT must not happen
        product_put_calls = [
            c for c in client.put.call_args_list if c.args and c.args[0] == "/products/42"
        ]
        self.assertEqual(product_put_calls, [])

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertEqual(len(payload["details"]["failed"]), 1)


class TestProductPutFailure(unittest.TestCase):
    """All uploads succeed but the final product PUT fails — uploads are
    already permanent (assets exist in Voog's library), surface them so the
    caller can manually re-link if desired."""

    def test_product_put_failure_surfaces_uploads(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        client.post.return_value = {"id": 201, "upload_url": "https://s3/up"}
        client.put.side_effect = [
            {"id": 201, "public_url": "u", "width": 1, "height": 1},  # confirm OK
            urllib.error.HTTPError("url", 422, "Unprocessable", {}, None),  # product PUT fails
        ]

        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        # Even on error, the orphan-asset breakdown must be visible to the
        # caller. The error response carries `details` with the upload state.
        details = payload.get("details", {})
        self.assertIn("uploaded", details)
        self.assertEqual(details["uploaded"][0]["asset_id"], 201)


class TestUnknownTool(unittest.TestCase):
    def test_unknown_name_returns_error(self):
        client = _make_client()
        result = products_images_tools.call_tool(
            "nonexistent",
            {},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — products_images joined to TOOL_GROUPS."""

    def test_products_images_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(products_images_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        from voog.mcp import server

        all_names = [tool.name for group in server.TOOL_GROUPS for tool in group.get_tools()]
        self.assertEqual(len(all_names), len(set(all_names)), f"Duplicate tool names: {all_names}")


class TestAllToolsRequireSite(unittest.TestCase):
    def test_all_tools_require_site(self):
        from voog.mcp.tools import products_images as mod

        for tool in mod.get_tools():
            self.assertIn(
                "site",
                tool.inputSchema.get("required", []),
                f"tool {tool.name} must require 'site'",
            )


if __name__ == "__main__":
    unittest.main()
