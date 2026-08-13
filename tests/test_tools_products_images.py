"""Tests for voog.mcp.tools.products_images.

Covers the 3-step Voog asset upload protocol via mocks:

    1. POST /assets (admin/api) → {id, upload_url}
    2. PUT upload_url with raw binary body (urllib.request, NOT VoogClient)
    3. PUT /assets/{id}/confirm (admin/api) → {public_url, width, height}

Then a final PUT /products/{id} (ecommerce_url) with flat
{image_id, assets:[{id:n}]} payload.

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

    def test_product_id_bool_rejected(self):
        # bool is int subclass — True/False must not slip through
        client = _make_client()
        result = products_images_tools.call_tool(
            "product_set_images",
            {"product_id": True, "files": ["/tmp/x.jpg"]},
            client,
        )
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("product_id", payload["error"])

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
        # First GET is the force-gate pre-flight; the second is the v1.5
        # order read-back, which must show the order that was just written.
        client.get.side_effect = [
            {"id": 42, "name": "Widget", "asset_ids": []},
            {"id": 42, "name": "Widget", "asset_ids": [200]},
        ]
        client.post.return_value = {
            "id": 200,
            "upload_url": "https://voog-test.s3.amazonaws.com/up200",
        }
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
        client.get.side_effect = [
            {"id": 42, "name": "Widget", "asset_ids": [99]},
            {"id": 42, "name": "Widget", "asset_ids": [200]},
        ]
        client.post.return_value = {
            "id": 200,
            "upload_url": "https://voog-test.s3.amazonaws.com/up200",
        }
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
        # Stateful: the order read-back must reflect what the product PUT
        # stored, or the verify loop would burn all 3 attempts.
        stored: dict = {"asset_ids": []}
        client.get.side_effect = lambda path, *a, **kw: {
            "id": 42,
            "name": "Widget",
            "asset_ids": list(stored["asset_ids"]),
        }

        post_ids = {"main.jpg": 201, "gallery.png": 202}

        def post_dispatch(path, body, **kwargs):
            aid = post_ids[body["filename"]]
            return {"id": aid, "upload_url": f"https://voog-test.s3.amazonaws.com/up{aid}"}

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
            stored["asset_ids"] = [a["id"] for a in (body or {}).get("assets", [])]
            return {
                "id": 42,
                "asset_ids": list(stored["asset_ids"]),
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

        # Final product PUT — flat payload {image_id, assets:[{id:n}]} on
        # ecommerce_url (NOT wrapped in {product: {...}} — different from
        # product_update). The field is `assets:[{id}]`, NOT `asset_ids` —
        # PUT-vs-POST gotcha: asset_ids on PUT silently keeps only the
        # hero image. uploaded order mirrors input order (parallel_map
        # preserves it), so main.jpg is always first → asset_id 201 is
        # the image_id.
        product_put_call = next(
            c for c in client.put.call_args_list if c.args and c.args[0] == "/products/42"
        )
        body = product_put_call.args[1]
        self.assertEqual(
            body,
            {"image_id": 201, "assets": [{"id": 201}, {"id": 202}]},
        )
        # Regression guard: asset_ids must NOT survive into the PUT body.
        self.assertNotIn("asset_ids", body)
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
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://voog-test.s3.amazonaws.com/up",
        }
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
            self.assertEqual(req_call.args[0], "https://voog-test.s3.amazonaws.com/up")
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
                return {"id": 201, "upload_url": "https://voog-test.s3.amazonaws.com/up201"}
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
                return {"id": aid, "upload_url": f"https://voog-test.s3.amazonaws.com/up{aid}"}
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
                return {"id": 201, "upload_url": "https://voog-test.s3.amazonaws.com/up201"}
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
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://voog-test.s3.amazonaws.com/up",
        }

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
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://voog-test.s3.amazonaws.com/up",
        }
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


class TestUploadUrlValidationCallerFlow(unittest.TestCase):
    """Caller-flow contract for the SSRF check.

    Validator behavior (scheme/host/env/dot-boundary semantics) lives in
    ``tests/test_upload_validation.py``. This test only verifies that the
    MCP tool wires the validator in correctly: a bad ``upload_url`` from
    the POST /assets response must short-circuit the upload and surface
    the failure through the existing ``failed[]`` orphan-recovery channel,
    so callers get the same UX as any other per-file upload failure.
    """

    def test_validation_failure_blocks_urlopen_and_surfaces_in_failed(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        # Voog returns a URL that fails validation (unknown host). The MCP
        # tool must NOT open the connection and must route the failure to
        # the per-file failed[] list with the asset_id so the caller can
        # recover the orphan.
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://attacker.example/upload",
        }
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )
            mock_urlopen.assert_not_called()

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        details = payload["details"]
        self.assertEqual(len(details["failed"]), 1)
        self.assertEqual(details["failed"][0]["filename"], "x.jpg")
        self.assertIn("upload_url", details["failed"][0]["error"].lower())


class TestGalleryOrderVerification(unittest.TestCase):
    """v1.5: one PUT does not reliably apply the gallery ORDER.

    Live on kolm-koma-2026 2026-08-13, 12 trials of a random 7-asset order:
    6 of 12 single PUTs stored the right assets in the wrong sequence and
    still returned 200. A second identical PUT fixed all 12. The tool's
    whole contract is "first file is the main image, rest are gallery", so
    reporting a clean ✓ over a shuffled gallery is the failure to prevent.
    """

    def _run(self, *, bad_writes: int, files=("a.jpg", "b.jpg", "c.jpg")):
        client = _make_client()
        ids = {name: 300 + n for n, name in enumerate(files)}
        stored: dict = {"asset_ids": [], "writes": 0}

        client.get.side_effect = lambda path, *a, **kw: {
            "id": 42,
            "asset_ids": list(stored["asset_ids"]),
        }
        client.post.side_effect = lambda path, body, **kw: {
            "id": ids[body["filename"]],
            "upload_url": f"https://voog-test.s3.amazonaws.com/up{ids[body['filename']]}",
        }

        def put_dispatch(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                aid = int(path.split("/")[2])
                return {"id": aid, "public_url": f"https://cdn/{aid}.jpg", "width": 8, "height": 6}
            sent = [a["id"] for a in (body or {}).get("assets", [])]
            stored["writes"] += 1
            # Voog's observed misbehaviour: right membership, one adjacent
            # pair transposed.
            if stored["writes"] <= bad_writes and len(sent) >= 2:
                sent = [sent[1], sent[0], *sent[2:]]
            stored["asset_ids"] = sent
            return {"id": 42, "asset_ids": list(sent)}

        client.put.side_effect = put_dispatch

        with tempfile.TemporaryDirectory() as tmp:
            paths = [str(_write_image(Path(tmp), name)) for name in files]
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": paths},
                    client,
                )
        return result, stored, [ids[n] for n in files]

    def test_partial_first_write_is_repaired_and_reported_verified(self):
        result, stored, wanted = self._run(bad_writes=1)
        payload = json.loads(result[-1].text)
        self.assertTrue(payload["order_verified"])
        self.assertEqual(stored["asset_ids"], wanted)
        self.assertEqual(stored["writes"], 2, "should have retried exactly once")
        self.assertNotIn("stored_asset_ids", payload)

    def test_order_that_never_takes_is_reported_as_an_error(self):
        # isError is the signal an LLM caller reliably branches on, and
        # media_set_set_assets already uses it for exactly this state.
        result, stored, wanted = self._run(bad_writes=99)
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        message = payload["error"]
        self.assertIn("ORDER", message)
        self.assertIn("3 attempt", message)
        # Membership IS correct — the message must say so rather than imply
        # the images were lost.
        self.assertIn("Every image IS linked", message)
        # And it must not invite the retry that re-uploads every file.
        self.assertIn("Do NOT re-run", message)
        details = payload["details"]
        self.assertFalse(details["order_verified"])
        self.assertEqual(sorted(details["stored_asset_ids"]), sorted(wanted))
        self.assertNotEqual(details["stored_asset_ids"], wanted)
        self.assertEqual(stored["writes"], 3, "attempt cap is 3")

    def test_clean_first_write_does_not_retry(self):
        _, stored, wanted = self._run(bad_writes=0)
        self.assertEqual(stored["writes"], 1)
        self.assertEqual(stored["asset_ids"], wanted)

    def test_single_image_still_verifies(self):
        # No pair to transpose — the loop must not spin on a 1-asset gallery.
        result, stored, wanted = self._run(bad_writes=1, files=("only.jpg",))
        payload = json.loads(result[-1].text)
        self.assertTrue(payload["order_verified"])
        self.assertEqual(stored["writes"], 1)
        self.assertEqual(payload["new_asset_ids"], wanted)


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


class TestConfirmRetry(unittest.TestCase):
    """S10 — step-3 confirm retries on TimeoutError up to 3 times.

    Mocks ``time.sleep`` to avoid the 3.5s of real sleep (0.5 + 1.0 + 2.0)
    that the worst-case test would otherwise burn. All tests complete in
    under 0.5s with the patch.
    """

    def _make_client(self):
        client = _make_client()
        # The order read-back (v1.5) reads the same endpoint as the force
        # pre-flight. Reflect the single uploaded asset so these tests keep
        # exercising the confirm retry rather than tripping over ordering.
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": [100]}
        client.post.return_value = {
            "id": 100,
            "upload_url": "https://voog-test.s3.amazonaws.com/up100",
        }
        return client

    def _write_one_image(self, tmpdir: Path):
        return _write_image(tmpdir, "img.jpg")

    def test_confirm_succeeds_first_try_no_sleep(self):
        # Happy path: confirm returns immediately; no retry, no sleep.
        client = self._make_client()

        # PUT handler: confirm + final product PUT.
        def _put(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                return {"public_url": "https://cdn/100.jpg", "width": 1, "height": 1}
            return {"id": 42}

        client.put.side_effect = _put

        with tempfile.TemporaryDirectory() as tmp:
            f = self._write_one_image(Path(tmp))
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.time.sleep") as mock_sleep,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(f)], "force": True},
                    client,
                )
        self.assertFalse(getattr(result, "isError", False))
        mock_sleep.assert_not_called()

    def test_confirm_retries_on_timeout_then_succeeds(self):
        client = self._make_client()
        confirm_calls = {"n": 0}

        def _put(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                confirm_calls["n"] += 1
                if confirm_calls["n"] == 1:
                    raise TimeoutError("S3 confirm timed out")
                return {"public_url": "https://cdn/100.jpg", "width": 1, "height": 1}
            return {"id": 42}

        client.put.side_effect = _put

        with tempfile.TemporaryDirectory() as tmp:
            f = self._write_one_image(Path(tmp))
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.time.sleep") as mock_sleep,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(f)], "force": True},
                    client,
                )
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(confirm_calls["n"], 2)
        # Slept exactly once, with 0.5s backoff before retry 1
        mock_sleep.assert_called_once_with(0.5)

    def test_confirm_retries_all_three_backoffs_then_succeeds(self):
        client = self._make_client()
        confirm_calls = {"n": 0}

        def _put(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                confirm_calls["n"] += 1
                if confirm_calls["n"] < 4:
                    raise TimeoutError(f"timeout #{confirm_calls['n']}")
                return {"public_url": "https://cdn/100.jpg"}
            return {"id": 42}

        client.put.side_effect = _put

        with tempfile.TemporaryDirectory() as tmp:
            f = self._write_one_image(Path(tmp))
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.time.sleep") as mock_sleep,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(f)], "force": True},
                    client,
                )
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(confirm_calls["n"], 4)
        # 3 sleeps: 0.5, 1.0, 2.0 — the documented backoff schedule
        self.assertEqual(
            [c.args[0] for c in mock_sleep.call_args_list],
            [0.5, 1.0, 2.0],
        )

    def test_confirm_exhausts_retries_then_propagates(self):
        client = self._make_client()

        def _put(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                raise TimeoutError("persistent timeout")
            return {"id": 42}

        client.put.side_effect = _put

        with tempfile.TemporaryDirectory() as tmp:
            f = self._write_one_image(Path(tmp))
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.time.sleep") as mock_sleep,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(f)], "force": True},
                    client,
                )
        # Timeout exhausted → per-file failed list → no product PUT →
        # tool reports isError.
        self.assertTrue(getattr(result, "isError", False))
        # 3 sleeps consumed before the final propagate
        self.assertEqual(mock_sleep.call_count, 3)

    def test_non_timeout_does_not_retry(self):
        # Any exception other than TimeoutError propagates immediately —
        # no extra sleep, no extra attempt. Mirrors _request's general
        # "no retry on write timeouts" policy: the confirm-specific retry
        # is narrow to TimeoutError, not "any failure".
        client = self._make_client()
        client.put.side_effect = urllib.error.HTTPError("u", 500, "Server Error", {}, None)

        with tempfile.TemporaryDirectory() as tmp:
            f = self._write_one_image(Path(tmp))
            with (
                patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen,
                patch("voog.mcp.tools.products_images.time.sleep") as mock_sleep,
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(f)], "force": True},
                    client,
                )
        self.assertTrue(getattr(result, "isError", False))
        mock_sleep.assert_not_called()


class TestConfirmIdempotency(unittest.TestCase):
    """H5 (v1.4 review): pin the empirical contract that S10 retry
    relies on.

    Race scenario the S10 retry guards against:
      1. PUT /assets/{id}/confirm reaches Voog
      2. Voog confirms the asset server-side
      3. TCP RST before response reaches the client → TimeoutError
      4. Retry fires the same PUT against the already-confirmed asset
      5. **If Voog returns 409/422 'already confirmed'**, the retry would
         exit with HTTPStatusError, orphan-recovery would fire on a
         legitimately-confirmed asset, and the operator would see
         "confirm failed" on a successful upload.

    Empirical probe against Stella OLD (2026-05-28) confirmed both
    PUT attempts return **HTTP 200** with the full asset payload.
    The only diff between attempts is the ``updated_at`` timestamp.
    See ``tests/fixtures/ecommerce/asset_confirm_idempotent.json``.

    This test loads that fixture and pins the contract: if Voog ever
    changes the post-confirm response to 409/422, this test goes red
    and the S10 retry needs to learn the new status-code branch.
    """

    def test_fixture_confirms_idempotency_both_200(self):
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "ecommerce"
            / "asset_confirm_idempotent.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertEqual(
            fixture["attempt_1"]["status"],
            200,
            "Voog must return 200 on first confirm — S10 retry assumes "
            "idempotent success on re-confirm.",
        )
        self.assertEqual(
            fixture["attempt_2"]["status"],
            200,
            "Voog must return 200 on second confirm of the same asset_id — "
            "S10 retry on TimeoutError would otherwise misclassify the "
            "post-race retry as failure and trigger orphan cleanup against "
            "a legitimately-confirmed asset. If this test goes red, see "
            "`docs/v1.4-code-review.md` finding H5 and add a 409/422 "
            "'already confirmed' status-code branch to products_images.py.",
        )

        a1 = fixture["attempt_1"]["response"]
        a2 = fixture["attempt_2"]["response"]
        self.assertEqual(a1["id"], a2["id"])
        self.assertEqual(a1["filename"], a2["filename"])
        self.assertEqual(a1["size"], a2["size"])
        # Only timestamps differ between attempts.
        self.assertNotEqual(a1["updated_at"], a2["updated_at"])


if __name__ == "__main__":
    unittest.main()


class TestWriteFailureIsReportedHonestly(unittest.TestCase):
    """The two blockers from the PR #141 review, pinned.

    Before v1.5 this tool made exactly one product PUT, so "an exception
    happened" and "the product was not touched" were the same statement and
    the error text was built on that. The retry loop broke the equivalence
    and the text was not revisited: a first write that landed followed by a
    failing retry reported "product NOT updated ... re-link manually", over
    a product whose images were already linked — and the suggested recovery
    (re-run) re-uploads every file as a fresh asset.
    """

    def _client_with_put_failing_on(self, failing_attempt: int, *, stored_after_write=True):
        client = _make_client()
        state = {"asset_ids": [], "product_writes": 0}
        client.get.side_effect = lambda path, *a, **kw: {
            "id": 42,
            "asset_ids": list(state["asset_ids"]),
        }
        client.post.side_effect = lambda path, body, **kw: {
            "id": 500,
            "upload_url": "https://voog-test.s3.amazonaws.com/up500",
        }

        def _put(path, body=None, **kwargs):
            if path.endswith("/confirm"):
                return {"id": 500, "public_url": "https://cdn/500.jpg", "width": 8, "height": 6}
            state["product_writes"] += 1
            if state["product_writes"] == failing_attempt:
                raise RuntimeError("502 Bad Gateway")
            if stored_after_write:
                # Wrong order on purpose, so the loop retries and hits the
                # failing attempt.
                state["asset_ids"] = [999, *[a["id"] for a in (body or {}).get("assets", [])]]
            return {"id": 42}

        client.put.side_effect = _put
        return client, state

    def _run(self, client):
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "a.jpg")
            with patch("voog.mcp.tools.products_images.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                return products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                )

    def test_first_write_fails_says_the_product_was_not_updated(self):
        client, state = self._client_with_put_failing_on(1)
        result = self._run(client)
        self.assertTrue(result.isError)
        message = json.loads(result.content[0].text)["error"]
        self.assertIn("NOT", message)
        self.assertEqual(state["product_writes"], 1)

    def test_a_failing_retry_does_not_claim_the_product_was_untouched(self):
        # Attempt 1 lands, attempt 2 raises. The product IS updated.
        client, state = self._client_with_put_failing_on(2)
        result = self._run(client)
        self.assertTrue(result.isError)
        message = json.loads(result.content[0].text)["error"]
        self.assertIn("WAS updated", message)
        self.assertNotIn("NOT updated", message)
        # And it must not send the caller down the orphan-cleanup path for a
        # problem they do not have, nor invite a re-upload.
        self.assertNotIn("re-link manually", message)
        self.assertIn("Do NOT re-run", message)
        self.assertEqual(state["product_writes"], 2)

    def test_read_back_failure_does_not_claim_the_images_are_linked(self):
        # The read-back is the ONLY thing that would catch a wrong-envelope
        # PUT keeping just the hero image, so "could not read it back" must
        # not be reported as "everything is linked, only the order is off".
        client = _make_client()
        reads = {"n": 0}

        def _get(path, *a, **kw):
            reads["n"] += 1
            if reads["n"] == 1:
                return {"id": 42, "asset_ids": []}  # force pre-flight
            raise RuntimeError("503 on read-back")

        client.get.side_effect = _get
        client.post.side_effect = lambda path, body, **kw: {
            "id": 600,
            "upload_url": "https://voog-test.s3.amazonaws.com/up600",
        }
        client.put.side_effect = lambda path, body=None, **kw: (
            {"id": 600, "public_url": "https://cdn/600.jpg", "width": 8, "height": 6}
            if path.endswith("/confirm")
            else {"id": 42}
        )
        result = self._run(client)
        self.assertTrue(result.isError)
        message = json.loads(result.content[0].text)["error"]
        self.assertIn("FAILED", message)
        self.assertIn("neither is", message)
        self.assertNotIn("Every image IS linked", message)
        details = json.loads(result.content[0].text)["details"]
        self.assertFalse(details["order_read_back"])
