"""Tests for voog.mcp.tools.cart_rules."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog.mcp.tools import cart_rules as crt

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
        names = sorted(t.name for t in crt.get_tools())
        self.assertEqual(
            names,
            [
                "cart_rule_create",
                "cart_rule_delete",
                "cart_rule_get",
                "cart_rule_update",
                "cart_rules_list",
            ],
        )


class TestCartRulesList(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("cart_rules_list")
        crt.call_tool("cart_rules_list", {}, client)
        client.get_all.assert_called_once_with("/cart_rules", base=client.ecommerce_url)


class TestCartRuleGet(unittest.TestCase):
    def test_fetches_by_id(self):
        client = _make_client()
        client.get.return_value = _load_fixture("cart_rule_get")
        crt.call_tool("cart_rule_get", {"cart_rule_id": 42}, client)
        client.get.assert_called_once_with("/cart_rules/42", base=client.ecommerce_url)

    def test_cart_rule_id_bool_rejected(self):
        client = _make_client()
        result = crt.call_tool("cart_rule_get", {"cart_rule_id": True}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)


class TestCartRuleCreate(unittest.TestCase):
    def _full_payload(self):
        return {
            "kind": "shipping_cost",
            "target_kind": "shipping_method",
            "target_id": 9001,
            "conditions": [
                {
                    "value": "100.0",
                    "comparator": ">=",
                    "field": "items_subtotal_amount",
                    "value_type": "decimal",
                }
            ],
            "result": {
                "value": "0.0",
                "field": "shipping_subtotal_amount",
                "value_type": "decimal",
            },
        }

    def test_envelope_shape(self):
        client = _make_client()
        client.post.return_value = _load_fixture("cart_rule_get")
        crt.call_tool("cart_rule_create", self._full_payload(), client)
        args, _ = client.post.call_args
        path, body = args[0], args[1]
        self.assertEqual(path, "/cart_rules")
        self.assertIn("cart_rule", body)
        self.assertEqual(body["cart_rule"]["kind"], "shipping_cost")
        self.assertIsInstance(body["cart_rule"]["conditions"], list)
        self.assertIsInstance(body["cart_rule"]["result"], dict)

    def test_requires_kind(self):
        payload = self._full_payload()
        del payload["kind"]
        client = _make_client()
        result = crt.call_tool("cart_rule_create", payload, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_result_dict(self):
        payload = self._full_payload()
        payload["result"] = "not-a-dict"
        client = _make_client()
        result = crt.call_tool("cart_rule_create", payload, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_conditions_must_be_list(self):
        payload = self._full_payload()
        payload["conditions"] = {"not": "a list"}
        client = _make_client()
        result = crt.call_tool("cart_rule_create", payload, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_target_id_bool_rejected(self):
        payload = self._full_payload()
        payload["target_id"] = True
        client = _make_client()
        result = crt.call_tool("cart_rule_create", payload, client)
        client.post.assert_not_called()
        self.assertTrue(result.isError)


class TestCartRuleUpdate(unittest.TestCase):
    def test_requires_at_least_one_field(self):
        client = _make_client()
        result = crt.call_tool("cart_rule_update", {"cart_rule_id": 42}, client)
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_partial_toggle_enabled(self):
        client = _make_client()
        client.put.return_value = {}
        crt.call_tool("cart_rule_update", {"cart_rule_id": 42, "enabled": False}, client)
        args, _ = client.put.call_args
        self.assertEqual(args[0], "/cart_rules/42")
        self.assertEqual(args[1], {"cart_rule": {"enabled": False}})


class TestCartRuleDelete(unittest.TestCase):
    def test_requires_force(self):
        client = _make_client()
        result = crt.call_tool("cart_rule_delete", {"cart_rule_id": 42}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = _make_client()
        client.delete.return_value = None
        crt.call_tool("cart_rule_delete", {"cart_rule_id": 42, "force": True}, client)
        client.delete.assert_called_once_with("/cart_rules/42", base=client.ecommerce_url)

    def test_annotations(self):
        ann = {t.name: t for t in crt.get_tools()}["cart_rule_delete"].annotations
        self.assertIs(ann.destructiveHint, True)


class TestServerToolRegistry(unittest.TestCase):
    def test_cart_rules_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(crt, server.TOOL_GROUPS)


if __name__ == "__main__":
    unittest.main()
