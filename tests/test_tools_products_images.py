"""Tests for voog_mcp.tools.products_images.

Covers the 3-step Voog asset upload protocol via mocks:

    1. POST /assets (admin/api) → {id, upload_url}
    2. PUT upload_url with raw binary body (urllib.request, NOT VoogClient)
    3. PUT /assets/{id}/confirm (admin/api) → {public_url, width, height}

Then a final PUT /products/{id} (ecommerce_url) with flat
{image_id, asset_ids} payload.

Mutating + creates new asset records — never run against live runnel.ee.
"""
import asyncio
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._test_helpers import _ann_get
from voog_mcp.tools import products_images as products_images_tools


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
        result = asyncio.run(products_images_tools.call_tool(
            "product_set_images",
            {"files": ["/tmp/x.jpg"]},
            client,
        ))
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("product_id", payload["error"])

    def test_empty_files_rejected(self):
        client = _make_client()
        result = asyncio.run(products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": []},
            client,
        ))
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)

    def test_relative_path_rejected(self):
        client = _make_client()
        result = asyncio.run(products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": ["relative/path.jpg"]},
            client,
        ))
        client.post.assert_not_called()
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)
        self.assertIn("absolute", payload["error"])

    def test_nonexistent_file_rejected(self):
        client = _make_client()
        result = asyncio.run(products_images_tools.call_tool(
            "product_set_images",
            {"product_id": 42, "files": ["/nonexistent/path/img.jpg"]},
            client,
        ))
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
            result = asyncio.run(products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(txt)]},
                client,
            ))
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
            result = asyncio.run(products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(pdf)]},
                client,
            ))
            client.post.assert_not_called()
            client.put.assert_not_called()


class TestForceGuard(unittest.TestCase):
    """Defensive opt-in like page_delete: refuses to replace existing images
    unless force=true."""

    def test_force_false_blocks_when_product_has_existing_images(self):
        client = _make_client()
        client.get.return_value = {
            "id": 42, "name": "Widget", "asset_ids": [100, 101],
        }
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "new.jpg")
            result = asyncio.run(products_images_tools.call_tool(
                "product_set_images",
                {"product_id": 42, "files": [str(img)]},
                client,
            ))
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
            {"id": 200, "public_url": "https://cdn/200.jpg", "width": 800, "height": 600},  # confirm
            {"id": 42, "asset_ids": [200], "image_id": 200},  # final product PUT
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "new.jpg")
            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                ))
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
            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)], "force": True},
                    client,
                ))
            payload = json.loads(result[-1].text)
            self.assertEqual(payload["old_asset_ids"], [99])
            self.assertEqual(payload["new_asset_ids"], [200])


class TestSuccessPath(unittest.TestCase):
    """All uploads succeed → final PUT goes through with correct asset_ids."""

    def test_three_step_upload_per_file_then_product_put(self):
        client = _make_client()
        # GET product (no existing images so force=false is fine)
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        # POST /assets called twice (one per file)
        client.post.side_effect = [
            {"id": 201, "upload_url": "https://s3.example.com/up201"},
            {"id": 202, "upload_url": "https://s3.example.com/up202"},
        ]
        # PUT /assets/{id}/confirm twice + PUT /products/42 once
        client.put.side_effect = [
            {"id": 201, "public_url": "https://cdn/201.jpg", "width": 1200, "height": 800},
            {"id": 202, "public_url": "https://cdn/202.jpg", "width": 1200, "height": 800},
            {"id": 42, "asset_ids": [201, 202], "image_id": 201},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "main.jpg")
            img2 = _write_image(tmpdir, "gallery.png")

            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img1), str(img2)]},
                    client,
                ))

        # POST /assets called once per file with admin/api default base
        self.assertEqual(client.post.call_count, 2)
        first_post_args = client.post.call_args_list[0]
        self.assertEqual(first_post_args.args[0], "/assets")
        body = first_post_args.args[1]
        self.assertEqual(body["filename"], "main.jpg")
        self.assertEqual(body["content_type"], "image/jpeg")
        self.assertEqual(body["size"], len(b"\x89PNG\r\n\x1a\nfake"))

        second_post_body = client.post.call_args_list[1].args[1]
        self.assertEqual(second_post_body["content_type"], "image/png")

        # urlopen called once per file (binary PUT to upload_url)
        self.assertEqual(mock_urlopen.call_count, 2)

        # PUT calls: 2× confirm + 1× product update
        self.assertEqual(client.put.call_count, 3)
        confirm1 = client.put.call_args_list[0]
        self.assertEqual(confirm1.args[0], "/assets/201/confirm")
        confirm2 = client.put.call_args_list[1]
        self.assertEqual(confirm2.args[0], "/assets/202/confirm")

        # Final product PUT — flat payload {image_id, asset_ids} on ecommerce_url
        # (NOT wrapped in {product: {...}} — different from product_update,
        # voog.py CLI confirms this shape)
        product_put = client.put.call_args_list[2]
        self.assertEqual(product_put.args[0], "/products/42")
        self.assertEqual(
            product_put.args[1],
            {"image_id": 201, "asset_ids": [201, 202]},
        )
        self.assertEqual(
            product_put.kwargs["base"],
            "https://test.example.com/admin/api/ecommerce/v1",
        )

        payload = json.loads(result[-1].text)
        self.assertEqual(payload["product_id"], 42)
        self.assertEqual(payload["new_asset_ids"], [201, 202])
        self.assertEqual(payload["old_asset_ids"], [])
        self.assertEqual(len(payload["uploaded"]), 2)
        self.assertEqual(payload["uploaded"][0]["filename"], "main.jpg")
        self.assertEqual(payload["uploaded"][0]["asset_id"], 201)
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
            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen, patch(
                "voog_mcp.tools.products_images.urllib.request.Request"
            ) as mock_request:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                ))
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
    """

    def test_one_upload_fails_product_not_updated(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        # First POST /assets succeeds, second fails (HTTP 500 from Voog)
        client.post.side_effect = [
            {"id": 201, "upload_url": "https://s3/up201"},
            urllib.error.HTTPError("url", 500, "Server Error", {}, None),
        ]
        # Confirm of the first one succeeds; no product PUT should follow
        client.put.return_value = {
            "id": 201, "public_url": "u", "width": 1, "height": 1,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "ok.jpg")
            img2 = _write_image(tmpdir, "fails.jpg")

            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img1), str(img2)]},
                    client,
                ))

        # PUT /products/{id} must NOT have been called
        product_put_calls = [
            c for c in client.put.call_args_list
            if c.args and c.args[0] == "/products/42"
        ]
        self.assertEqual(product_put_calls, [])

        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        # Error path — orphan uploads surfaced via `details`
        self.assertIn("error", payload)
        details = payload["details"]
        self.assertEqual(len(details["uploaded"]), 1)
        self.assertEqual(details["uploaded"][0]["asset_id"], 201)
        self.assertEqual(len(details["failed"]), 1)
        self.assertEqual(details["failed"][0]["filename"], "fails.jpg")

    def test_s3_upload_failure_captured_per_file(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "asset_ids": []}
        client.post.return_value = {"id": 201, "upload_url": "https://s3/up"}

        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                # S3 returns 403 → upload failed
                mock_urlopen.return_value.__enter__.return_value.status = 403
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                ))

        # Confirm should not be called if S3 upload failed
        confirm_calls = [
            c for c in client.put.call_args_list
            if c.args and "confirm" in c.args[0]
        ]
        self.assertEqual(confirm_calls, [])
        # Product PUT must not happen
        product_put_calls = [
            c for c in client.put.call_args_list
            if c.args and c.args[0] == "/products/42"
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
            with patch(
                "voog_mcp.tools.products_images.urllib.request.urlopen"
            ) as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.status = 200
                result = asyncio.run(products_images_tools.call_tool(
                    "product_set_images",
                    {"product_id": 42, "files": [str(img)]},
                    client,
                ))

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
        result = asyncio.run(products_images_tools.call_tool(
            "nonexistent", {}, client,
        ))
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("error", payload)


class TestServerToolRegistry(unittest.TestCase):
    """Phase C contract — products_images joined to TOOL_GROUPS."""

    def test_products_images_in_tool_groups(self):
        from voog_mcp import server
        self.assertIn(products_images_tools, server.TOOL_GROUPS)

    def test_no_tool_name_collisions(self):
        from voog_mcp import server
        all_names = [
            tool.name
            for group in server.TOOL_GROUPS
            for tool in group.get_tools()
        ]
        self.assertEqual(len(all_names), len(set(all_names)),
                         f"Duplicate tool names: {all_names}")


if __name__ == "__main__":
    unittest.main()
