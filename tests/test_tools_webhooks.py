"""Tests for voog.mcp.tools.webhooks — webhook CRUD."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import webhooks as wt


class TestGetTools(unittest.TestCase):
    def test_one_tool_registered_after_task1(self):
        # Tasks 2-4 add webhook_create / webhook_update / webhook_delete.
        # This sentinel grows per task to keep each task self-contained green.
        names = sorted(t.name for t in wt.get_tools())
        self.assertEqual(names, ["webhooks_list"])


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


class TestServerToolRegistry(unittest.TestCase):
    def test_webhooks_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(wt, server.TOOL_GROUPS)
