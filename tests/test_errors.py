"""Tests for voog_mcp.errors."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.errors import error_response, success_response


class TestErrorResponse(unittest.TestCase):
    def test_error_response_basic(self):
        result = error_response("something failed")
        self.assertEqual(len(result), 1)
        payload = json.loads(result[0].text)
        self.assertEqual(payload["error"], "something failed")
        self.assertNotIn("details", payload)

    def test_error_response_with_details(self):
        result = error_response("bad input", details={"field": "page_id"})
        payload = json.loads(result[0].text)
        self.assertEqual(payload["error"], "bad input")
        self.assertEqual(payload["details"], {"field": "page_id"})

    def test_error_response_unicode_preserved(self):
        result = error_response("Tundmatu lehekülg: õ")
        # The raw text should NOT contain \u escape sequences
        self.assertIn("Tundmatu lehekülg: õ", result[0].text)


class TestSuccessResponse(unittest.TestCase):
    def test_success_response_no_summary(self):
        result = success_response({"id": 1})
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
