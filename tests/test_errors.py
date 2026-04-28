"""Tests for voog.errors."""
import json
import unittest

from mcp.types import CallToolResult, TextContent

from voog.errors import error_response, success_response


class TestErrorResponse(unittest.TestCase):
    def test_error_response_returns_call_tool_result_with_iserror(self):
        # MCP spec § 7: tool errors must surface via isError=True so clients
        # can distinguish them from successful responses. The SDK call_tool
        # decorator passes a CallToolResult through untouched, but wraps a
        # plain list[TextContent] with isError=False.
        result = error_response("something failed")
        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIsInstance(result.content[0], TextContent)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["error"], "something failed")
        self.assertNotIn("details", payload)

    def test_error_response_with_details(self):
        result = error_response("bad input", details={"field": "page_id"})
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["error"], "bad input")
        self.assertEqual(payload["details"], {"field": "page_id"})

    def test_error_response_unicode_preserved(self):
        result = error_response("Tundmatu lehekülg: õ")
        # The raw text should NOT contain \u escape sequences
        self.assertIn("Tundmatu lehekülg: õ", result.content[0].text)


class TestSuccessResponse(unittest.TestCase):
    def test_success_response_returns_list_of_text_content(self):
        # success_response stays as list[TextContent]; the SDK's call_tool
        # decorator wraps it with isError=False, which is the desired default.
        result = success_response({"id": 1})
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0].text)
        self.assertEqual(payload, {"id": 1})

    def test_success_response_with_summary(self):
        result = success_response({"id": 1}, summary="Found one item")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].text, "Found one item")
        payload = json.loads(result[1].text)
        self.assertEqual(payload, {"id": 1})

    def test_success_response_unicode_preserved(self):
        result = success_response({"name": "Sõnastõlge"})
        payload = json.loads(result[0].text)
        self.assertEqual(payload["name"], "Sõnastõlge")
