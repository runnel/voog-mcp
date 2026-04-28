"""Tests for voog_mcp.resources._helpers."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
    prefix_matcher,
    text_response,
)


class TestParseId(unittest.TestCase):
    def test_valid_positive_integer(self):
        self.assertEqual(parse_id("42", "voog://x/42", group_name="x"), 42)

    def test_large_id(self):
        self.assertEqual(
            parse_id("152377", "voog://pages/152377", group_name="pages"),
            152377,
        )

    def test_non_integer_raises_with_group_and_uri(self):
        with self.assertRaises(ValueError) as ctx:
            parse_id("abc", "voog://pages/abc", group_name="pages")
        msg = str(ctx.exception)
        self.assertIn("pages", msg)
        self.assertIn("'voog://pages/abc'", msg)
        self.assertIn("invalid id", msg)

    def test_non_integer_chains_underlying_exception(self):
        # `from e` should preserve the int() failure traceback
        with self.assertRaises(ValueError) as ctx:
            parse_id("xyz", "voog://x/xyz", group_name="x")
        self.assertIsNotNone(ctx.exception.__cause__)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_zero_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            parse_id("0", "voog://x/0", group_name="x")
        self.assertIn("must be positive", str(ctx.exception))

    def test_negative_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            parse_id("-5", "voog://x/-5", group_name="x")
        self.assertIn("must be positive", str(ctx.exception))

    def test_value_check_suppresses_implicit_chain(self):
        # `from None` should suppress Python's implicit "during handling of"
        # context (there is none here since the int() succeeded — but the
        # explicit `from None` makes the intent obvious to readers)
        with self.assertRaises(ValueError) as ctx:
            parse_id("0", "voog://x/0", group_name="x")
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_group_name_appears_in_message(self):
        # Each call site passes a different group_name — error must reflect it
        with self.assertRaises(ValueError) as ctx:
            parse_id("abc", "voog://articles/abc", group_name="articles")
        self.assertIn("articles resource", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            parse_id("abc", "voog://layouts/abc", group_name="layouts")
        self.assertIn("layouts resource", str(ctx.exception))


class TestJsonResponse(unittest.TestCase):
    def test_returns_single_read_resource_contents(self):
        result = json_response({"key": "value"})
        self.assertEqual(len(result), 1)

    def test_mime_type_is_application_json(self):
        result = json_response([])
        self.assertEqual(result[0].mime_type, "application/json")

    def test_content_is_valid_json(self):
        data = {"id": 1, "name": "test"}
        result = json_response(data)
        parsed = json.loads(result[0].content)
        self.assertEqual(parsed, data)

    def test_indent_is_2(self):
        result = json_response({"key": "value"})
        # Indented output has newlines + 2-space indent
        self.assertIn("\n  ", result[0].content)

    def test_ensure_ascii_false_preserves_non_ascii(self):
        # Estonian characters must round-trip without \uXXXX escaping
        result = json_response({"title": "Tõnu Söömlais"})
        self.assertIn("Tõnu", result[0].content)
        self.assertNotIn("\\u", result[0].content)

    def test_handles_empty_list(self):
        result = json_response([])
        self.assertEqual(result[0].content, "[]")

    def test_handles_nested_structures(self):
        data = {"items": [{"id": 1}, {"id": 2}], "meta": {"total": 2}}
        result = json_response(data)
        parsed = json.loads(result[0].content)
        self.assertEqual(parsed, data)


class TestTextResponse(unittest.TestCase):
    def test_returns_single_read_resource_contents(self):
        result = text_response("hello", mime_type="text/plain")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ReadResourceContents)

    def test_mime_type_propagates(self):
        plain = text_response("x", mime_type="text/plain")
        html = text_response("<p>x</p>", mime_type="text/html")
        self.assertEqual(plain[0].mime_type, "text/plain")
        self.assertEqual(html[0].mime_type, "text/html")

    def test_empty_string_preserved(self):
        result = text_response("", mime_type="text/plain")
        self.assertEqual(result[0].content, "")

    def test_content_passes_through_unchanged(self):
        body = "<!DOCTYPE html>\n<p>Tõnu</p>"
        result = text_response(body, mime_type="text/html")
        self.assertEqual(result[0].content, body)


class TestPrefixMatcher(unittest.TestCase):
    def test_exact_prefix_matches(self):
        matches = prefix_matcher("voog://pages")
        self.assertTrue(matches("voog://pages"))

    def test_slashed_subpath_matches(self):
        matches = prefix_matcher("voog://pages")
        self.assertTrue(matches("voog://pages/42"))
        self.assertTrue(matches("voog://pages/42/contents"))

    def test_unrelated_uri_does_not_match(self):
        matches = prefix_matcher("voog://pages")
        self.assertFalse(matches("voog://layouts"))
        self.assertFalse(matches("voog://layouts/42"))

    def test_pseudo_prefix_collision_rejected(self):
        # The slash check is the whole point — voog://pagesx must NOT be
        # claimed by a "voog://pages" group.
        matches = prefix_matcher("voog://pages")
        self.assertFalse(matches("voog://pagesx"))
        self.assertFalse(matches("voog://pagesx/42"))

    def test_each_call_returns_independent_matcher(self):
        # Two matchers built from different prefixes must not see each other's
        # URIs.
        m_pages = prefix_matcher("voog://pages")
        m_articles = prefix_matcher("voog://articles")
        self.assertTrue(m_pages("voog://pages/1"))
        self.assertFalse(m_pages("voog://articles/1"))
        self.assertTrue(m_articles("voog://articles/1"))
        self.assertFalse(m_articles("voog://pages/1"))


if __name__ == "__main__":
    unittest.main()
