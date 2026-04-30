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


# ---------------------------------------------------------------------------
# cmd_list and cmd_product — non-image commands
# ---------------------------------------------------------------------------


def _list_args() -> argparse.Namespace:
    return argparse.Namespace()


def _product_args(product_id: int, fields: list[str]) -> argparse.Namespace:
    return argparse.Namespace(product_id=product_id, fields=fields)


class TestProductsList(unittest.TestCase):
    """Tests for `voog products` (cmd_list)."""

    def test_uses_ecommerce_base_with_translations_include(self):
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "slug": "widget", "name": "Widget"},
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = products_cli.cmd_list(_list_args(), client)
        self.assertEqual(rc, 0)
        client.get_all.assert_called_once_with(
            "/products",
            base=client.ecommerce_url,
            params={"include": "translations"},
        )
        self.assertIn("Widget", stdout.getvalue())
        self.assertIn("Total: 1", stdout.getvalue())

    def test_strips_zero_width_chars_from_name(self):
        # Voog admins occasionally paste names with zero-width spaces
        # that break terminal alignment. The list command sanitizes
        # them out for display.
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "slug": "widget", "name": "Widget﻿"},
            {"id": 2, "slug": "tool", "name": "Tool​"},
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            products_cli.cmd_list(_list_args(), client)
        out = stdout.getvalue()
        self.assertNotIn("﻿", out)
        self.assertNotIn("​", out)
        self.assertIn("Widget", out)
        self.assertIn("Tool", out)


class TestProductGetUpdate(unittest.TestCase):
    """Tests for `voog product` (cmd_product) — GET when no fields,
    PUT translations payload when fields given."""

    def test_no_fields_prints_full_product_json(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "name": "Widget", "translations": {}}
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = products_cli.cmd_product(_product_args(42, []), client)
        self.assertEqual(rc, 0)
        client.get.assert_called_once_with(
            "/products/42",
            base=client.ecommerce_url,
            params={"include": "variant_types,variants,translations"},
        )
        # JSON dump output present
        self.assertIn('"id": 42', stdout.getvalue())

    def test_odd_number_of_fields_returns_two(self):
        # Field/value pairs come in even pairs. Odd count = usage error.
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = products_cli.cmd_product(
                _product_args(42, ["name-et", "Foo", "name-en"]),  # missing value
                client,
            )
        self.assertEqual(rc, 2)
        self.assertIn("key/value pairs", stderr.getvalue())
        client.put.assert_not_called()

    def test_unknown_field_attribute_returns_two(self):
        # Only `name` and `slug` are settable via this command.
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = products_cli.cmd_product(
                _product_args(42, ["price-et", "10"]),  # not allowed
                client,
            )
        self.assertEqual(rc, 2)
        self.assertIn("name, slug", stderr.getvalue())
        client.put.assert_not_called()

    def test_field_without_dash_returns_two(self):
        client = _make_client()
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = products_cli.cmd_product(
                _product_args(42, ["name", "Foo"]),  # no language code
                client,
            )
        self.assertEqual(rc, 2)
        self.assertIn("'name-et'", stderr.getvalue())

    def test_translation_payload_shape(self):
        client = _make_client()
        client.put.return_value = {"id": 42, "name": "Widget", "slug": "widget"}
        client.get.return_value = {"translations": {"name": {"et": "W", "en": "W"}}}
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = products_cli.cmd_product(
                _product_args(
                    42,
                    ["name-et", "Vidin", "name-en", "Widget", "slug-et", "vidin"],
                ),
                client,
            )
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with(
            "/products/42",
            {
                "product": {
                    "translations": {
                        "name": {"et": "Vidin", "en": "Widget"},
                        "slug": {"et": "vidin"},
                    }
                }
            },
            base=client.ecommerce_url,
        )

    def test_only_one_attribute_omits_empty_dict(self):
        # Setting only name-et leaves slug untouched — payload should
        # not include an empty `slug` translations dict.
        client = _make_client()
        client.put.return_value = {"id": 42}
        client.get.return_value = {}
        with patch("sys.stdout", new_callable=io.StringIO):
            products_cli.cmd_product(_product_args(42, ["name-et", "Vidin"]), client)
        payload = client.put.call_args.args[1]
        self.assertEqual(payload["product"]["translations"], {"name": {"et": "Vidin"}})


if __name__ == "__main__":
    unittest.main()
