"""Tests for voog.mcp.tools.orders - read + PII redact."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog._payloads import ORDER_PUBLIC_FIELDS
from voog.mcp.tools import orders as ot

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ecommerce"


def _load_fixture(name: str):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)["response"]


def _make_client():
    client = MagicMock()
    client.ecommerce_url = "https://stella.example.com/admin/api/ecommerce/v1"
    return client


class TestGetTools(unittest.TestCase):
    def test_two_tools_registered(self):
        names = sorted(t.name for t in ot.get_tools())
        self.assertEqual(names, ["order_get", "orders_list"])


class TestOrdersList(unittest.TestCase):
    def test_no_filters_no_params(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("orders_list")
        ot.call_tool("orders_list", {}, client)
        called = client.get_all.call_args
        self.assertEqual(called[0][0], "/orders")
        self.assertIs(called[1]["base"], client.ecommerce_url)
        self.assertIsNone(called[1]["params"])

    def test_filters_map_to_q_keys(self):
        client = _make_client()
        client.get_all.return_value = []
        ot.call_tool(
            "orders_list",
            {
                "status": "created",
                "payment_status": "paid",
                "created_after": "2026-01-01",
                "created_before": "2026-02-01",
            },
            client,
        )
        params = client.get_all.call_args[1]["params"]
        self.assertEqual(params["q.order.status.$eq"], "created")
        self.assertEqual(params["q.order.payment_status.$eq"], "paid")
        self.assertEqual(params["q.order.created_at.$gteq"], "2026-01-01")
        self.assertEqual(params["q.order.created_at.$lteq"], "2026-02-01")

    def test_pii_stripped_by_default(self):
        client = _make_client()
        client.get_all.return_value = _load_fixture("orders_list")
        result = ot.call_tool("orders_list", {}, client)
        items = json.loads(result[1].text)
        for o in items:
            for k in o:
                self.assertIn(
                    k,
                    ORDER_PUBLIC_FIELDS,
                    f"unexpected key {k!r} survived default redaction",
                )
            self.assertNotIn("customer", o)
            self.assertNotIn("billing_address", o)
            self.assertNotIn("shipping_address", o)

    def test_pii_kept_with_include_pii(self):
        client = _make_client()
        raw = _load_fixture("orders_list")
        client.get_all.return_value = raw
        # H2 (v1.4 review): include_pii=True now requires force=True.
        result = ot.call_tool(
            "orders_list",
            {"include_pii": True, "force": True},
            client,
        )
        items = json.loads(result[1].text)
        # When include_pii=True we return the raw fixture (no key dropping).
        # Items should now contain the PII keys that were stripped above.
        if raw and "customer" in raw[0]:
            self.assertIn("customer", items[0])

    def test_include_pii_without_force_rejected(self):
        # H2 (v1.4 review): include_pii=True without force=True is an
        # error — LLM-side PII-exfiltration gate.
        client = _make_client()
        client.get_all.return_value = _load_fixture("orders_list")
        result = ot.call_tool("orders_list", {"include_pii": True}, client)
        self.assertTrue(result.isError)
        # Make sure the API call didn't actually go out (fail-closed).
        client.get_all.assert_not_called()
        # Error message should name the gate so the operator/LLM
        # understands the required next step.
        err = result.content[0].text
        self.assertIn("force=true", err.lower())
        self.assertIn("pii", err.lower())

    def test_include_pii_with_force_false_rejected(self):
        # Explicit force=False is the same as omitted force.
        client = _make_client()
        client.get_all.return_value = _load_fixture("orders_list")
        result = ot.call_tool(
            "orders_list",
            {"include_pii": True, "force": False},
            client,
        )
        self.assertTrue(result.isError)
        client.get_all.assert_not_called()

    def test_force_ignored_when_include_pii_false(self):
        # force=True alone (no include_pii) is harmless — the gate only
        # fires when include_pii is requested. PII still stripped.
        client = _make_client()
        client.get_all.return_value = _load_fixture("orders_list")
        result = ot.call_tool("orders_list", {"force": True}, client)
        items = json.loads(result[1].text)
        for o in items:
            self.assertNotIn("customer", o)


class TestOrderGet(unittest.TestCase):
    def test_fetches_by_id(self):
        client = _make_client()
        client.get.return_value = _load_fixture("order_get")
        ot.call_tool("order_get", {"order_id": 42}, client)
        client.get.assert_called_once_with("/orders/42", base=client.ecommerce_url)

    def test_order_id_bool_rejected(self):
        client = _make_client()
        result = ot.call_tool("order_get", {"order_id": True}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_pii_stripped_by_default(self):
        client = _make_client()
        client.get.return_value = _load_fixture("order_get")
        result = ot.call_tool("order_get", {"order_id": 42}, client)
        # Two-element list: summary + payload JSON.
        order = json.loads(result[-1].text)
        for k in order:
            self.assertIn(k, ORDER_PUBLIC_FIELDS)
        self.assertNotIn("customer", order)
        self.assertNotIn("billing_address", order)

    def test_summary_indicates_pii_stripped(self):
        client = _make_client()
        client.get.return_value = _load_fixture("order_get")
        result = ot.call_tool("order_get", {"order_id": 42}, client)
        self.assertIn("PII stripped", result[0].text)

    def test_summary_omits_pii_marker_when_include_pii(self):
        client = _make_client()
        client.get.return_value = _load_fixture("order_get")
        # H2 (v1.4 review): include_pii=True now requires force=True.
        result = ot.call_tool(
            "order_get",
            {"order_id": 42, "include_pii": True, "force": True},
            client,
        )
        self.assertNotIn("PII stripped", result[0].text)

    def test_include_pii_without_force_rejected(self):
        # H2 (v1.4 review): same gate as orders_list.
        client = _make_client()
        client.get.return_value = _load_fixture("order_get")
        result = ot.call_tool(
            "order_get",
            {"order_id": 42, "include_pii": True},
            client,
        )
        self.assertTrue(result.isError)
        client.get.assert_not_called()
        err = result.content[0].text
        self.assertIn("force=true", err.lower())

    def test_annotations(self):
        ann = {t.name: t for t in ot.get_tools()}["order_get"].annotations
        self.assertIs(ann.readOnlyHint, True)
        # H2 (v1.4 review): destructiveHint=True surfaces the PII gate
        # in MCP host approval UI when include_pii=true is requested.
        # Spec-strict hosts may treat readOnlyHint=True as authoritative
        # and skip the prompt; handler-side force-gate is the load-
        # bearing defense regardless.
        self.assertIs(ann.destructiveHint, True)

    def test_orders_list_annotations(self):
        # H2 mirror — orders_list has the same include_pii surface, so
        # it carries the same annotation pair. Drift between the two
        # would be a surprise.
        ann = {t.name: t for t in ot.get_tools()}["orders_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, True)


class TestServerToolRegistry(unittest.TestCase):
    def test_orders_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(ot, server.TOOL_GROUPS)


if __name__ == "__main__":
    unittest.main()
