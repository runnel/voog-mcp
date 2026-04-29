"""Tests for voog.cli.commands.products — caller-flow contract for the
3-step asset upload, with focus on the SSRF-validation wiring.

Validator behavior lives in ``tests/test_upload_validation.py``; this file
only verifies that ``cmd_product_image`` invokes the validator at the right
moment (after POST /assets, before urlopen) and surfaces orphan recovery
guidance to stderr when validation fails.
"""

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import products as products_cli


def _make_client():
    client = MagicMock()
    client.host = "example.com"
    client.base_url = "https://example.com/admin/api"
    client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
    return client


def _write_image(dirpath: Path, name: str, body: bytes = b"\x89PNG\r\n\x1a\nfake") -> Path:
    p = dirpath / name
    p.write_bytes(body)
    return p


def _args(product_id: int, files: list[Path]) -> argparse.Namespace:
    return argparse.Namespace(product_id=product_id, files=files)


class TestUploadValidationWiring(unittest.TestCase):
    def test_bad_upload_url_blocks_urlopen(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        # Voog returns a non-allowlisted host — validator must reject before
        # any bytes are sent.
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://attacker.example/upload",
        }
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with (
                patch("voog.cli.commands.products.urllib.request.urlopen") as mock_urlopen,
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                rc = products_cli.cmd_product_image(_args(42, [img]), client)
            mock_urlopen.assert_not_called()
        # Confirm step (PUT /assets/{id}/confirm) must also be skipped — the
        # asset stays in the un-confirmed orphan state.
        client.put.assert_not_called()
        self.assertNotEqual(rc, 0)

    def test_bad_upload_url_prints_orphan_warning_to_stderr(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://attacker.example/upload",
        }
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with (
                patch("voog.cli.commands.products.urllib.request.urlopen"),
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                products_cli.cmd_product_image(_args(42, [img]), client)
            err = stderr.getvalue()

        # Caller must learn: which asset_id is orphaned, what to do about it,
        # and that the validation (not the network) failed.
        self.assertIn("201", err)  # asset_id surfaced
        self.assertIn("orphan", err.lower())  # explicit orphan keyword
        # Recovery hint — DELETE /assets/{id} or admin UI cleanup
        self.assertTrue(
            "/assets/" in err or "admin" in err.lower(),
            f"stderr should hint at cleanup path; got: {err!r}",
        )

    def test_first_upload_validation_failure_aborts_remaining(self):
        # CLI flow is sequential first-failure-break (matches existing
        # behavior — different from MCP's collect-then-decide). When the
        # first file's upload_url fails validation, the second POST must
        # not happen — no second orphan to clean up.
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://attacker.example/upload",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            img1 = _write_image(tmpdir, "first.jpg")
            img2 = _write_image(tmpdir, "second.jpg")
            with (
                patch("voog.cli.commands.products.urllib.request.urlopen"),
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                products_cli.cmd_product_image(_args(42, [img1, img2]), client)

        # Only the first file's POST should have happened.
        self.assertEqual(client.post.call_count, 1)


class TestHappyPath(unittest.TestCase):
    """Allowlisted upload_url should pass validation and complete the
    3-step protocol normally — sanity check that the validator wiring
    didn't break the success path."""

    def test_valid_amazonaws_host_completes_upload(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "asset_ids": []}
        client.post.return_value = {
            "id": 201,
            "upload_url": "https://voog-prod.s3.eu-west-1.amazonaws.com/u/201?sig=abc",
        }
        client.put.side_effect = [
            {"id": 201, "public_url": "https://cdn/201.jpg", "width": 800, "height": 600},
            {
                "id": 42,
                "asset_ids": [201],
                "image_id": 201,
                "image": {"width": 800, "height": 600},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img = _write_image(Path(tmp), "x.jpg")
            with (
                patch("voog.cli.commands.products.urllib.request.urlopen") as mock_urlopen,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                mock_urlopen.return_value.__enter__.return_value.status = 200
                rc = products_cli.cmd_product_image(_args(42, [img]), client)
            mock_urlopen.assert_called_once()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
