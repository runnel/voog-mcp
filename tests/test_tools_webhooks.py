"""Tests for voog.mcp.tools.webhooks — webhook CRUD."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import webhooks as wt


class TestGetTools(unittest.TestCase):
    def test_four_tools_registered(self):
        names = sorted(t.name for t in wt.get_tools())
        self.assertEqual(
            names,
            ["webhook_create", "webhook_delete", "webhook_update", "webhooks_list"],
        )


class TestWebhooksList(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in wt.get_tools()}
        self.assertIn("webhooks_list", names)

    def test_returns_simplified(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "enabled": True,
                "target": "order",
                "event": "paid",
                "url": "https://example.com/hook",
                "target_id": None,
                "source": "api",
                "description": "stripe ack",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        ]
        result = wt.call_tool("webhooks_list", {}, client)
        client.get_all.assert_called_once_with("/webhooks")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 1)
        self.assertEqual(items[0]["target"], "order")
        # Dropped fields per simplify_webhooks contract
        self.assertNotIn("created_at", items[0])
        self.assertNotIn("updated_at", items[0])
        self.assertNotIn("source", items[0])

    def test_annotations(self):
        tools = {t.name: t for t in wt.get_tools()}
        ann = tools["webhooks_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestWebhookCreate(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in wt.get_tools()}
        self.assertIn("webhook_create", names)

    def test_minimum_payload(self):
        client = MagicMock()
        client.post.return_value = {
            "id": 99,
            "target": "order",
            "event": "paid",
            "url": "https://example.com/hook",
            "enabled": True,
        }
        wt.call_tool(
            "webhook_create",
            {
                "target": "order",
                "event": "paid",
                "url": "https://example.com/hook",
            },
            client,
        )
        client.post.assert_called_once_with(
            "/webhooks",
            {
                "target": "order",
                "event": "paid",
                "url": "https://example.com/hook",
            },
        )

    def test_full_payload(self):
        client = MagicMock()
        client.post.return_value = {"id": 99}
        wt.call_tool(
            "webhook_create",
            {
                "target": "ticket",
                "event": "create",
                "url": "https://example.com/hook",
                "enabled": False,
                "target_id": 5,
                "source": "api",
                "description": "support sync",
            },
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("webhook", sent_body)
        self.assertEqual(sent_body["target"], "ticket")
        self.assertEqual(sent_body["target_id"], 5)
        self.assertIs(sent_body["enabled"], False)
        self.assertEqual(sent_body["description"], "support sync")

    def test_no_envelope_wrapper(self):
        # Regression guard against future "wrap me in {webhook: ...}" drift.
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "form", "event": "submit", "url": "https://x"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("webhook", sent_body)
        self.assertIn("target", sent_body)

    def test_requires_target(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"event": "submit", "url": "https://x"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_event(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "form", "url": "https://x"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_url(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "form", "event": "submit"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in wt.get_tools()}
        ann = tools["webhook_create"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, False)


class TestWebhookUpdate(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in wt.get_tools()}
        self.assertIn("webhook_update", names)

    def test_partial_update_url(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "https://new.example.com/hook"},
            client,
        )
        client.put.assert_called_once_with(
            "/webhooks/7",
            {"url": "https://new.example.com/hook"},
        )

    def test_disable_webhook(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "enabled": False},
            client,
        )
        # `False is not None` → field IS sent (legitimate disable).
        client.put.assert_called_once_with("/webhooks/7", {"enabled": False})

    def test_full_payload(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {
                "webhook_id": 7,
                "target": "order",
                "event": "shipped",
                "url": "https://x",
                "enabled": True,
                "target_id": 99,
                "source": "user",
                "description": "operations alert",
            },
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("webhook", sent_body)
        self.assertNotIn("webhook_id", sent_body)
        self.assertEqual(sent_body["target"], "order")
        self.assertEqual(sent_body["target_id"], 99)

    def test_requires_at_least_one_field(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_no_envelope_wrapper(self):
        client = MagicMock()
        client.put.return_value = {"id": 1}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 1, "enabled": True},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("webhook", sent_body)

    def test_annotations(self):
        tools = {t.name: t for t in wt.get_tools()}
        ann = tools["webhook_update"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestWebhookDelete(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in wt.get_tools()}
        self.assertIn("webhook_delete", names)

    def test_requires_force(self):
        client = MagicMock()
        result = wt.call_tool("webhook_delete", {"webhook_id": 7}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        wt.call_tool(
            "webhook_delete",
            {"webhook_id": 7, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/webhooks/7")

    def test_annotations(self):
        tools = {t.name: t for t in wt.get_tools()}
        ann = tools["webhook_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestWebhookCreateTargetIdValidation(unittest.TestCase):
    """Issue 1 — target_id must be a plain int, not a bool."""

    def test_target_id_true_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook", "target_id": True},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("bool", result.content[0].text)
        self.assertIn("integer", result.content[0].text)

    def test_target_id_false_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook", "target_id": False},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("bool", result.content[0].text)

    def test_target_id_valid_int_succeeds(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook", "target_id": 5},
            client,
        )
        client.post.assert_called_once()
        sent_body = client.post.call_args[0][1]
        self.assertEqual(sent_body["target_id"], 5)

    def test_target_id_omitted_no_error(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook"},
            client,
        )
        client.post.assert_called_once()

    def test_target_id_none_no_error(self):
        # None is filtered by the `is not None` guard — treated as omitted.
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook", "target_id": None},
            client,
        )
        client.post.assert_called_once()


class TestWebhookUpdateTargetIdValidation(unittest.TestCase):
    """Issue 1 — target_id bool rejection in webhook_update."""

    def test_target_id_true_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "target_id": True},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("bool", result.content[0].text)
        self.assertIn("integer", result.content[0].text)

    def test_target_id_false_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "target_id": False},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("bool", result.content[0].text)

    def test_target_id_valid_int_succeeds(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "target_id": 99},
            client,
        )
        client.put.assert_called_once()
        sent_body = client.put.call_args[0][1]
        self.assertEqual(sent_body["target_id"], 99)

    def test_target_id_omitted_no_error(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "enabled": True},
            client,
        )
        client.put.assert_called_once()

    def test_target_id_none_no_error(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "enabled": True, "target_id": None},
            client,
        )
        client.put.assert_called_once()


class TestWebhookCreateUrlValidation(unittest.TestCase):
    """Issue 2 — url must start with http:// or https://."""

    def test_ftp_url_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "ftp://x"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("http", result.content[0].text)

    def test_javascript_url_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "javascript:alert(1)"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_bare_string_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "not-a-url"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_http_url_succeeds(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "http://example.com/hook"},
            client,
        )
        client.post.assert_called_once()

    def test_https_url_succeeds(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "https://example.com/hook"},
            client,
        )
        client.post.assert_called_once()

    def test_uppercase_http_url_succeeds(self):
        # RFC 3986 §3.1 — scheme is case-insensitive.
        client = MagicMock()
        client.post.return_value = {"id": 1}
        wt.call_tool(
            "webhook_create",
            {"target": "order", "event": "paid", "url": "HTTP://example.com/hook"},
            client,
        )
        client.post.assert_called_once()


class TestWebhookUpdateUrlValidation(unittest.TestCase):
    """Issue 2 — url scheme validation in webhook_update."""

    def test_ftp_url_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "ftp://x"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)
        self.assertIn("http", result.content[0].text)

    def test_javascript_url_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "javascript:alert(1)"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_bare_string_returns_error(self):
        client = MagicMock()
        result = wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "not-a-url"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_http_url_succeeds(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "http://example.com/hook"},
            client,
        )
        client.put.assert_called_once()

    def test_https_url_succeeds(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "https://example.com/hook"},
            client,
        )
        client.put.assert_called_once()

    def test_uppercase_https_url_succeeds(self):
        # RFC 3986 §3.1 — scheme is case-insensitive.
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "HTTPS://example.com/hook"},
            client,
        )
        client.put.assert_called_once()

    def test_mixedcase_https_url_succeeds(self):
        # RFC 3986 §3.1 — scheme is case-insensitive.
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "url": "HtTpS://example.com/hook"},
            client,
        )
        client.put.assert_called_once()

    def test_url_none_no_error(self):
        # None means url not being updated — skip validation.
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "enabled": True, "url": None},
            client,
        )
        client.put.assert_called_once()

    def test_url_omitted_no_error(self):
        client = MagicMock()
        client.put.return_value = {"id": 7}
        wt.call_tool(
            "webhook_update",
            {"webhook_id": 7, "enabled": False},
            client,
        )
        client.put.assert_called_once()


class TestServerToolRegistry(unittest.TestCase):
    def test_webhooks_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(wt, server.TOOL_GROUPS)
