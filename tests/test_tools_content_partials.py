"""Tests for voog.mcp.tools.content_partials — PUT /content_partials/{id}."""

import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import content_partials as content_partials_tools


class TestGetTools(unittest.TestCase):
    def test_one_tool_registered(self):
        names = [t.name for t in content_partials_tools.get_tools()]
        self.assertEqual(names, ["content_partial_update"])

    def test_annotations(self):
        tool = content_partials_tools.get_tools()[0]
        ann = tool.annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestContentPartialUpdate(unittest.TestCase):
    def test_update_body_only(self):
        client = MagicMock()
        client.put.return_value = {"id": 5, "body": "<p>new</p>"}
        content_partials_tools.call_tool(
            "content_partial_update",
            {"content_partial_id": 5, "body": "<p>new</p>"},
            client,
        )
        client.put.assert_called_once_with(
            "/content_partials/5", {"body": "<p>new</p>"}
        )

    def test_update_metainfo_only(self):
        client = MagicMock()
        client.put.return_value = {"id": 5, "metainfo": {"type": "video"}}
        content_partials_tools.call_tool(
            "content_partial_update",
            {"content_partial_id": 5, "metainfo": {"type": "video"}},
            client,
        )
        client.put.assert_called_once_with(
            "/content_partials/5", {"metainfo": {"type": "video"}}
        )

    def test_update_body_and_metainfo(self):
        client = MagicMock()
        client.put.return_value = {"id": 5}
        content_partials_tools.call_tool(
            "content_partial_update",
            {
                "content_partial_id": 5,
                "body": "<p>x</p>",
                "metainfo": {"type": "custom"},
            },
            client,
        )
        client.put.assert_called_once_with(
            "/content_partials/5",
            {"body": "<p>x</p>", "metainfo": {"type": "custom"}},
        )

    def test_update_requires_at_least_one_field(self):
        client = MagicMock()
        result = content_partials_tools.call_tool(
            "content_partial_update",
            {"content_partial_id": 5},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_update_no_envelope_wrapper(self):
        # Per the Voog content_partials doc, PUT body is bare {body,
        # metainfo} — NO {"content_partial": {...}} wrapper. Regression
        # guard against accidental envelope drift in payload-builder
        # consolidation work.
        client = MagicMock()
        client.put.return_value = {}
        content_partials_tools.call_tool(
            "content_partial_update",
            {"content_partial_id": 5, "body": "x"},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("content_partial", sent_body)
        self.assertIn("body", sent_body)

    def test_update_unknown_tool_returns_error(self):
        client = MagicMock()
        result = content_partials_tools.call_tool("bogus", {}, client)
        self.assertTrue(result.isError)


if __name__ == "__main__":
    unittest.main()
