"""Tests for voog.mcp.tools.discounts."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog.mcp.tools import discounts as dt

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ecommerce"


def _load_fixture(name: str):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)["response"]


def _make_client():
    client = MagicMock()
    client.ecommerce_url = "https://stella.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_five_tools_registered(self):
        names = sorted(t.name for t in dt.get_tools())
        self.assertEqual(
            names,
            [
                "discount_create",
                "discount_delete",
                "discount_get",
                "discount_update",
                "discounts_list",
            ],
        )


class TestDiscountsList(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("discounts_list")
        dt.call_tool("discounts_list", {}, client)
        client.get_all.assert_called_once_with("/discounts", base=client.ecommerce_url)


class TestDiscountGet(unittest.TestCase):
    def test_fetches_by_id(self):
        client = _make_client()
        client.get.return_value = _load_fixture("discount_get")
        dt.call_tool("discount_get", {"discount_id": 42}, client)
        client.get.assert_called_once_with("/discounts/42", base=client.ecommerce_url)

    def test_discount_id_bool_rejected(self):
        client = _make_client()
        result = dt.call_tool("discount_get", {"discount_id": True}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)


class TestDiscountCreate(unittest.TestCase):
    def test_requires_code(self):
        client = _make_client()
        result = dt.call_tool("discount_create", {"name": "X"}, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_envelope_shape(self):
        client = _make_client()
        client.post.return_value = _load_fixture("discount_get")
        dt.call_tool(
            "discount_create",
            {
                "code": "SUMMER",
                "name": "Summer sale",
                "amount": 10.0,
                "amount_mode": "net",
                "discount_type": "percentage",
                "status": "open",
                "applies_to": "cart",
            },
            client,
        )
        args, _ = client.post.call_args
        path, body = args[0], args[1]
        self.assertEqual(path, "/discounts")
        self.assertEqual(body["discount"]["code"], "SUMMER")
        self.assertEqual(body["discount"]["amount_mode"], "net")
        self.assertEqual(body["discount"]["discount_type"], "percentage")

    def test_redemption_limit_bool_rejected(self):
        client = _make_client()
        result = dt.call_tool("discount_create", {"code": "X", "redemption_limit": True}, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)


class TestDiscountUpdate(unittest.TestCase):
    def test_requires_at_least_one_field(self):
        client = _make_client()
        result = dt.call_tool("discount_update", {"discount_id": 42}, client)
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_partial_envelope(self):
        client = _make_client()
        client.put.return_value = {}
        dt.call_tool("discount_update", {"discount_id": 42, "name": "Renamed"}, client)
        args, _ = client.put.call_args
        self.assertEqual(args[0], "/discounts/42")
        self.assertEqual(args[1], {"discount": {"name": "Renamed"}})


class TestDiscountDelete(unittest.TestCase):
    def test_requires_force(self):
        client = _make_client()
        result = dt.call_tool("discount_delete", {"discount_id": 42}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = _make_client()
        client.delete.return_value = None
        dt.call_tool("discount_delete", {"discount_id": 42, "force": True}, client)
        client.delete.assert_called_once_with("/discounts/42", base=client.ecommerce_url)

    def test_annotations(self):
        ann = {t.name: t for t in dt.get_tools()}["discount_delete"].annotations
        self.assertIs(ann.destructiveHint, True)


class TestDiscountEnumValidation(unittest.TestCase):
    """Empirically-verified closed enum sets (Stella OLD probe 2026-05-27).
    Client-side guard surfaces typos as clean local errors instead of
    Voog 422 round-trips.
    """

    def _full_payload(self, **overrides):
        body = {
            "code": "TEST",
            "amount": 5,
            "amount_mode": "net",
            "discount_type": "fixed",
            "status": "open",
            "applies_to": "cart",
            "currency": "EUR",
        }
        body.update(overrides)
        return body

    def test_invalid_status_rejected(self):
        # plan's old 'active' would have failed at Voog with 422; now
        # caught locally.
        client = _make_client()
        result = dt.call_tool("discount_create", self._full_payload(status="active"), client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_invalid_amount_mode_rejected(self):
        # 'percent' looks reasonable but Voog only accepts 'net' / 'gross'.
        client = _make_client()
        result = dt.call_tool("discount_create", self._full_payload(amount_mode="percent"), client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_invalid_discount_type_rejected(self):
        client = _make_client()
        result = dt.call_tool(
            "discount_create", self._full_payload(discount_type="absolute"), client
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_invalid_applies_to_rejected(self):
        client = _make_client()
        result = dt.call_tool("discount_create", self._full_payload(applies_to="all"), client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_valid_enum_combinations_accepted(self):
        from voog.mcp.tools.discounts import (
            VALID_DISCOUNT_AMOUNT_MODE,
            VALID_DISCOUNT_APPLIES_TO,
            VALID_DISCOUNT_STATUS,
            VALID_DISCOUNT_TYPE,
        )

        # Smoke: every empirically-valid enum value passes the client-side
        # guard. Catches typos in the frozensets that would otherwise
        # only show up under live API usage.
        for status in VALID_DISCOUNT_STATUS:
            for amount_mode in VALID_DISCOUNT_AMOUNT_MODE:
                for dtype in VALID_DISCOUNT_TYPE:
                    for applies_to in VALID_DISCOUNT_APPLIES_TO:
                        client = _make_client()
                        client.post.return_value = {"id": 1, "code": "X"}
                        result = dt.call_tool(
                            "discount_create",
                            self._full_payload(
                                status=status,
                                amount_mode=amount_mode,
                                discount_type=dtype,
                                applies_to=applies_to,
                            ),
                            client,
                        )
                        client.post.assert_called_once()
                        self.assertFalse(getattr(result, "isError", False))

    def test_update_also_validates_enums(self):
        client = _make_client()
        result = dt.call_tool(
            "discount_update",
            {"discount_id": 1, "status": "active"},  # invalid
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)


class TestServerToolRegistry(unittest.TestCase):
    def test_discounts_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(dt, server.TOOL_GROUPS)


if __name__ == "__main__":
    unittest.main()
