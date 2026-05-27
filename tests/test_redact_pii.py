"""Tests for voog._payloads.redact_pii - whitelist-based order PII strip."""

import json
import unittest
from pathlib import Path

from voog._payloads import (
    ORDER_CART_RULE_APPLIED_PUBLIC_FIELDS,
    ORDER_ITEM_PUBLIC_FIELDS,
    ORDER_PUBLIC_FIELDS,
    ORDER_SHIPPING_METHOD_PUBLIC_FIELDS,
    redact_pii,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ecommerce"


def _load_fixture(name: str):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)["response"]


class TestRedactPii(unittest.TestCase):
    def test_include_pii_true_returns_input(self):
        order = {"id": 1, "customer": {"email": "leak@example.com"}}
        self.assertIs(redact_pii(order, include_pii=True), order)

    def test_none_passthrough(self):
        self.assertIsNone(redact_pii(None))

    def test_non_dict_non_list_passthrough(self):
        self.assertEqual(redact_pii("hello"), "hello")
        self.assertEqual(redact_pii(42), 42)

    def test_dict_drops_unknown_keys(self):
        order = {
            "id": 1,
            "status": "paid",
            "customer": {"email": "leak@example.com", "phone": "+372 0000 0000"},
            "billing_address": {"street": "Leak St 1"},
            "shipping_address": {"city": "Leak City"},
            "note": "leaky free-form note",
            "return_url": "https://example.com/leak?token=abc",
            "urls": {"invoice_url": "https://example.com/leak?signature=abc"},
            "gateway_transaction_id": "psp-tx-leak",
            "shipping_method_option": "Omniva Tallinn Suburb Box 7 (leaks location)",
            "external_shipment_attrs": {"label_url": "leak"},
            "custom_field_values": {"size": "M"},
        }
        out = redact_pii(order)
        self.assertEqual(set(out.keys()), {"id", "status"})

    def test_dict_keeps_whitelisted(self):
        order = {f: f"value-{f}" for f in ORDER_PUBLIC_FIELDS}
        out = redact_pii(order)
        self.assertEqual(set(out.keys()), set(ORDER_PUBLIC_FIELDS))

    def test_items_inner_whitelist(self):
        order = {
            "id": 1,
            "items": [
                {
                    "id": 11,
                    "kind": "regular",
                    "status": "paid",
                    "product_id": 99,
                    "price": "5",
                    "effective_price": "5",
                    "original_price": "5",
                    "quantity": 2,
                    "amount": "10",
                    "subtotal_amount": "10",
                    "tax_amount": "2",
                    "tax_rate": "20.0",
                    "has_item_discount": False,
                    "product_name": "Bag X",
                    # NON-whitelist:
                    "note": "customer-entered leak",
                    "product": {"sku": "SKU-1", "name": "Bag X internal"},
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-01",
                }
            ],
        }
        out = redact_pii(order)
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(set(item.keys()), set(ORDER_ITEM_PUBLIC_FIELDS))
        self.assertNotIn("note", item)
        self.assertNotIn("product", item)
        self.assertNotIn("created_at", item)

    def test_shipping_method_inner_whitelist(self):
        order = {
            "id": 1,
            "shipping_method": {
                "id": 2,
                "name": "Omniva",
                "description": "Pakipoint",
                "amount": "4.99",
                "tax_rate": "22.0",
                "delivery_method": "courier",
                # Drop:
                "option": "Tallinn Pärnu mnt 555 Box 7",  # location-revealing
            },
        }
        out = redact_pii(order)
        self.assertEqual(
            set(out["shipping_method"].keys()), set(ORDER_SHIPPING_METHOD_PUBLIC_FIELDS)
        )
        self.assertNotIn("option", out["shipping_method"])

    def test_cart_rules_applied_false_passthrough(self):
        order = {"id": 1, "cart_rules_applied": False}
        out = redact_pii(order)
        self.assertEqual(out["cart_rules_applied"], False)

    def test_cart_rules_applied_list_inner_whitelist(self):
        order = {
            "id": 1,
            "cart_rules_applied": [
                {
                    "id": 7,
                    "code": "FREESHIP",
                    "name": "Free Shipping",
                    "kind": "shipping_cost",
                    "value": 0,
                    "applied_amount": "-4.95",
                    # Drop these hypothetical PII fields:
                    "applied_to_email": "leak@example.com",
                    "customer_ip": "1.2.3.4",
                }
            ],
        }
        out = redact_pii(order)
        rule = out["cart_rules_applied"][0]
        self.assertEqual(set(rule.keys()), set(ORDER_CART_RULE_APPLIED_PUBLIC_FIELDS))

    def test_list_of_orders(self):
        orders = [
            {"id": 1, "status": "paid", "customer": {"email": "a@example.com"}},
            {"id": 2, "status": "pending", "customer": {"email": "b@example.com"}},
        ]
        out = redact_pii(orders)
        self.assertEqual(len(out), 2)
        for o in out:
            self.assertNotIn("customer", o)

    def test_against_real_fixture_orders_list(self):
        orders = _load_fixture("orders_list")
        if not orders:
            self.skipTest("orders_list fixture is empty")
        out = redact_pii(orders)
        self.assertEqual(len(out), len(orders))
        for o in out:
            for key in o:
                self.assertIn(
                    key,
                    ORDER_PUBLIC_FIELDS,
                    f"unexpected key {key!r} survived redaction (top-level drift)",
                )
            self.assertNotIn("customer", o)
            self.assertNotIn("billing_address", o)
            self.assertNotIn("shipping_address", o)
            self.assertNotIn("note", o)
            self.assertNotIn("return_url", o)
            self.assertNotIn("gateway_transaction_id", o)
            self.assertNotIn("custom_field_values", o)
            sm = o.get("shipping_method")
            if isinstance(sm, dict):
                for k in sm:
                    self.assertIn(
                        k,
                        ORDER_SHIPPING_METHOD_PUBLIC_FIELDS,
                        f"unexpected shipping_method.{k!r} survived",
                    )
                self.assertNotIn("option", sm)

    def test_against_real_fixture_order_get(self):
        order = _load_fixture("order_get")
        out = redact_pii(order)
        for key in out:
            self.assertIn(
                key,
                ORDER_PUBLIC_FIELDS,
                f"unexpected key {key!r} survived redaction (whitelist drift)",
            )
        for pii_key in (
            "customer",
            "billing_address",
            "shipping_address",
            "note",
            "return_url",
            "urls",
            "gateway_transaction_id",
            "shipping_method_option",
            "external_shipment_attrs",
            "custom_field_values",
        ):
            self.assertNotIn(pii_key, out, f"{pii_key} leaked")
        # items[] inner walk
        for item in out.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            for k in item:
                self.assertIn(
                    k,
                    ORDER_ITEM_PUBLIC_FIELDS,
                    f"unexpected items[].{k!r} survived",
                )
            self.assertNotIn("note", item)
            self.assertNotIn("product", item)

    def test_against_real_fixture_include_pii_keeps_customer(self):
        order = _load_fixture("order_get")
        out = redact_pii(order, include_pii=True)
        self.assertIn("customer", out)
        self.assertIn("billing_address", out)
        # When include_pii=True we return the input value unchanged.
        self.assertIs(out, order)

    def test_whitelist_is_immutable(self):
        self.assertIsInstance(ORDER_PUBLIC_FIELDS, frozenset)
        self.assertIsInstance(ORDER_ITEM_PUBLIC_FIELDS, frozenset)
        self.assertIsInstance(ORDER_SHIPPING_METHOD_PUBLIC_FIELDS, frozenset)
        self.assertIsInstance(ORDER_CART_RULE_APPLIED_PUBLIC_FIELDS, frozenset)


if __name__ == "__main__":
    unittest.main()
