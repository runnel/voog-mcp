"""Tests for require_int and require_force shared validators in _helpers.

These two helpers will be used by Tasks T2-T5 (v1.3 pre-release) to replace
inline bool-rejection and force-gate patterns scattered across tool modules.
"""

import unittest

from voog.mcp.tools._helpers import require_force, require_int


class TestRequireInt(unittest.TestCase):
    """require_int: returns None for valid ints, error string otherwise."""

    # --- valid ints ---

    def test_positive_int_returns_none(self):
        self.assertIsNone(require_int("page_id", 1, tool_name="tool_x"))

    def test_zero_returns_none(self):
        self.assertIsNone(require_int("page_id", 0, tool_name="tool_x"))

    def test_negative_int_returns_none(self):
        self.assertIsNone(require_int("page_id", -1, tool_name="tool_x"))

    def test_large_int_returns_none(self):
        self.assertIsNone(require_int("element_id", 99999, tool_name="element_get"))

    # --- bools rejected (bool is a Python int subclass) ---

    def test_true_returns_error(self):
        err = require_int("page_id", True, tool_name="tool_x")
        self.assertIsNotNone(err)
        self.assertIsInstance(err, str)

    def test_false_returns_error(self):
        err = require_int("page_id", False, tool_name="tool_x")
        self.assertIsNotNone(err)
        self.assertIsInstance(err, str)

    # --- other non-int types rejected ---

    def test_float_returns_error(self):
        err = require_int("page_id", 1.0, tool_name="tool_x")
        self.assertIsNotNone(err)

    def test_numeric_string_returns_error(self):
        err = require_int("page_id", "1", tool_name="tool_x")
        self.assertIsNotNone(err)

    def test_empty_string_returns_error(self):
        err = require_int("page_id", "", tool_name="tool_x")
        self.assertIsNotNone(err)

    def test_none_returns_error(self):
        err = require_int("page_id", None, tool_name="tool_x")
        self.assertIsNotNone(err)

    def test_list_returns_error(self):
        err = require_int("page_id", [], tool_name="tool_x")
        self.assertIsNotNone(err)

    def test_dict_returns_error(self):
        err = require_int("page_id", {}, tool_name="tool_x")
        self.assertIsNotNone(err)

    # --- error message content ---

    def test_error_message_contains_tool_name(self):
        err = require_int("page_id", "bad", tool_name="article_create")
        self.assertIn("article_create", err)

    def test_error_message_contains_field_name(self):
        err = require_int("element_definition_id", "bad", tool_name="element_create")
        self.assertIn("element_definition_id", err)

    def test_error_message_contains_type_name_for_bool(self):
        err = require_int("page_id", True, tool_name="tool_x")
        self.assertIn("bool", err)

    def test_error_message_contains_type_name_for_float(self):
        err = require_int("page_id", 1.5, tool_name="tool_x")
        self.assertIn("float", err)

    def test_error_message_contains_type_name_for_string(self):
        err = require_int("page_id", "hello", tool_name="tool_x")
        self.assertIn("str", err)

    def test_error_message_contains_type_name_for_none(self):
        err = require_int("page_id", None, tool_name="tool_x")
        self.assertIn("NoneType", err)

    def test_error_message_mentions_integer(self):
        err = require_int("page_id", "bad", tool_name="tool_x")
        self.assertIn("integer", err)

    def test_error_message_format_tool_colon_field(self):
        # Must follow the {tool_name}: {name} ... convention used across this codebase
        err = require_int("my_id", "x", tool_name="my_tool")
        self.assertTrue(err.startswith("my_tool:"), f"expected 'my_tool: ...' prefix, got: {err!r}")
        self.assertIn("my_id", err)

    def test_error_message_contains_value_repr_for_string(self):
        # Value repr is included so callers can see the exact bad input without re-reading the payload
        err = require_int("page_id", "hello", tool_name="tool_x")
        self.assertIn("'hello'", err)

    def test_error_message_contains_value_repr_for_float(self):
        err = require_int("page_id", 3.14, tool_name="tool_x")
        self.assertIn("3.14", err)

    def test_error_message_contains_value_repr_for_bool(self):
        err = require_int("page_id", True, tool_name="tool_x")
        self.assertIn("True", err)

    def test_error_message_contains_value_repr_for_none(self):
        err = require_int("page_id", None, tool_name="tool_x")
        self.assertIn("None", err)


class TestRequireForce(unittest.TestCase):
    """require_force: returns None when force is truthy, error string otherwise."""

    # --- force present and truthy → None ---

    def test_force_true_returns_none(self):
        self.assertIsNone(
            require_force({"force": True}, tool_name="webhook_delete", target_desc="webhook 42")
        )

    def test_force_truthy_string_returns_none(self):
        # Python truthiness: non-empty string is truthy. Matches existing
        # inline pattern `if not arguments.get("force")` which is truthy-based.
        self.assertIsNone(
            require_force({"force": "true"}, tool_name="webhook_delete", target_desc="webhook 42")
        )

    def test_force_nonzero_int_returns_none(self):
        self.assertIsNone(
            require_force({"force": 1}, tool_name="tool_x", target_desc="item 5")
        )

    # --- force absent or falsy → error string ---

    def test_force_false_returns_error(self):
        err = require_force({"force": False}, tool_name="webhook_delete", target_desc="webhook 42")
        self.assertIsNotNone(err)
        self.assertIsInstance(err, str)

    def test_force_missing_returns_error(self):
        err = require_force({}, tool_name="page_delete", target_desc="page 99")
        self.assertIsNotNone(err)

    def test_force_none_returns_error(self):
        err = require_force({"force": None}, tool_name="article_delete", target_desc="article 7")
        self.assertIsNotNone(err)

    def test_force_zero_returns_error(self):
        err = require_force({"force": 0}, tool_name="tool_x", target_desc="item 5")
        self.assertIsNotNone(err)

    # --- error message content ---

    def test_error_message_contains_tool_name(self):
        err = require_force({}, tool_name="redirect_delete", target_desc="rule 10")
        self.assertIn("redirect_delete", err)

    def test_error_message_contains_target_desc(self):
        err = require_force({}, tool_name="element_delete", target_desc="element 55")
        self.assertIn("element 55", err)

    def test_error_message_mentions_force(self):
        err = require_force({}, tool_name="page_delete", target_desc="page 1")
        self.assertIn("force", err)

    def test_error_message_format_tool_colon_prefix(self):
        # Must follow the {tool_name}: ... convention
        err = require_force({}, tool_name="my_tool", target_desc="widget 3")
        self.assertTrue(err.startswith("my_tool:"), f"expected 'my_tool: ...' prefix, got: {err!r}")

    # --- hint parameter ---

    def test_hint_appears_in_error_when_provided(self):
        err = require_force(
            {},
            tool_name="language_delete",
            target_desc="language 2",
            hint="Run site_snapshot first to confirm.",
        )
        self.assertIn("Run site_snapshot first to confirm.", err)

    def test_no_hint_message_has_no_trailing_hint_text(self):
        err = require_force({}, tool_name="redirect_delete", target_desc="rule 10", hint=None)
        # Just verify it returns a sensible message without crashing; we don't
        # prescribe exact end characters, but it should not contain "None".
        self.assertIsNotNone(err)
        self.assertNotIn("None", err)

    def test_hint_none_vs_no_hint_kwarg_produce_same_result(self):
        err_explicit_none = require_force(
            {}, tool_name="webhook_delete", target_desc="webhook 1", hint=None
        )
        err_no_kwarg = require_force({}, tool_name="webhook_delete", target_desc="webhook 1")
        self.assertEqual(err_explicit_none, err_no_kwarg)


if __name__ == "__main__":
    unittest.main()
