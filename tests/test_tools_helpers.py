"""Tests for voog.mcp.tools._helpers shared primitives."""

import json
import tempfile
import unittest
from pathlib import Path

from voog.mcp.tools._helpers import _validate_data_key, validate_output_dir, write_json


class TestValidateOutputDir(unittest.TestCase):
    def test_empty_returns_error(self):
        err = validate_output_dir("", tool_name="t", param_name="output_dir")
        self.assertIn("non-empty", err)
        self.assertIn("t:", err)
        self.assertIn("output_dir", err)

    def test_relative_path_returns_error(self):
        err = validate_output_dir("relative/dir", tool_name="t", param_name="target_dir")
        self.assertIn("absolute", err)
        self.assertIn("target_dir", err)
        self.assertIn("relative/dir", err)

    def test_absolute_path_returns_none(self):
        self.assertIsNone(validate_output_dir("/abs/path", tool_name="t", param_name="output_dir"))

    def test_param_name_appears_in_both_error_paths(self):
        empty_err = validate_output_dir("", tool_name="x", param_name="my_dir")
        rel_err = validate_output_dir("rel", tool_name="x", param_name="my_dir")
        self.assertIn("my_dir", empty_err)
        self.assertIn("my_dir", rel_err)


class TestWriteJson(unittest.TestCase):
    def test_writes_pretty_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            write_json(path, {"key": "väärtus", "list": [1, 2]})
            text = path.read_text(encoding="utf-8")
            # ensure_ascii=False keeps Estonian chars un-escaped
            self.assertIn("väärtus", text)
            # indent=2
            self.assertIn("\n  ", text)
            # Round-trip parses to identical structure
            self.assertEqual(json.loads(text), {"key": "väärtus", "list": [1, 2]})


class TestValidateDataKey(unittest.TestCase):
    """Defence-in-depth checks on user-supplied data keys interpolated into URL paths.

    The key/path validators are the only line of defence between
    Claude-generated arguments and the Voog Admin API. Percent-encoded
    bypasses (``%2F``, ``%23``, ``%3F``) and case variants of reserved
    prefixes have to be caught client-side — Apache and many other backends
    normalise ``%2F → /`` before routing, so a key that looks safe on the
    Python side can still alter URL structure server-side.
    """

    def test_accepts_plain_key(self):
        self.assertIsNone(_validate_data_key("my_setting", tool_name="t"))

    def test_rejects_empty(self):
        err = _validate_data_key("", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("non-empty", err)

    def test_rejects_whitespace_only(self):
        err = _validate_data_key("   ", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("non-empty", err)

    def test_rejects_internal_prefix(self):
        err = _validate_data_key("internal_secret", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("internal_", err)

    def test_rejects_uppercase_internal_prefix(self):
        # Case-insensitive — Voog server treats keys case-insensitively for
        # the protected ``internal_`` namespace; mixed-case variants must
        # not slip past the client check.
        for variant in ("INTERNAL_x", "Internal_foo", "InTeRnAl_y"):
            with self.subTest(variant=variant):
                err = _validate_data_key(variant, tool_name="t")
                self.assertIsNotNone(err, f"{variant!r} should be rejected")
                self.assertIn("internal_", err)

    def test_rejects_slash(self):
        err = _validate_data_key("foo/bar", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("/", err)

    def test_rejects_question_mark(self):
        err = _validate_data_key("foo?bar", tool_name="t")
        self.assertIsNotNone(err)

    def test_rejects_hash(self):
        err = _validate_data_key("foo#bar", tool_name="t")
        self.assertIsNotNone(err)

    def test_rejects_dotdot_segment(self):
        err = _validate_data_key("..", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("..", err)

    def test_rejects_percent_encoded_slash(self):
        # %2F (and lowercase %2f) decode to '/'. Apache and many backends
        # normalise this server-side, so the key must be rejected even
        # though the raw string contains no literal '/'.
        for variant in ("foo%2Fbar", "foo%2fbar"):
            with self.subTest(variant=variant):
                err = _validate_data_key(variant, tool_name="t")
                self.assertIsNotNone(err, f"{variant!r} should be rejected")

    def test_rejects_percent_encoded_question_mark(self):
        # %3F decodes to '?' — would split the path/query boundary.
        err = _validate_data_key("foo%3Fbar", tool_name="t")
        self.assertIsNotNone(err)

    def test_rejects_percent_encoded_hash(self):
        # %23 decodes to '#' — fragment marker.
        err = _validate_data_key("foo%23bar", tool_name="t")
        self.assertIsNotNone(err)

    def test_rejects_double_encoded_traversal(self):
        # Same double-decode threat as raw.py: the key is interpolated
        # into a URL path (``/site/data/{key}``), so an intermediate
        # proxy that decodes a second time before routing turns
        # ``%252e%252e`` into literal ``..``.
        err = _validate_data_key("%252e%252e", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("..", err)


class TestValidateDataKeyURLSafety(unittest.TestCase):
    """PR #109 follow-up: reject keys that would surface as urlopen errors."""

    def test_space_in_key_rejected(self):
        err = _validate_data_key("hex color", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("hex color", err)

    def test_unicode_key_rejected(self):
        err = _validate_data_key("πood", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("πood", err)

    def test_at_sign_rejected(self):
        # @ is URL-safe in some contexts but ambiguous in path segments.
        err = _validate_data_key("user@home", tool_name="t")
        self.assertIsNotNone(err)

    def test_plus_rejected(self):
        # + means " " in form-encoded bodies; ambiguous in path.
        err = _validate_data_key("a+b", tool_name="t")
        self.assertIsNotNone(err)

    def test_long_key_rejected(self):
        long_key = "a" * 129
        err = _validate_data_key(long_key, tool_name="t")
        self.assertIsNotNone(err)

    def test_safe_keys_still_accepted(self):
        for k in (
            "color",
            "hex_color",
            "menu-position",
            "x.y",
            "v1.2.3",
            "Item123",
            "a" * 128,  # exactly 128 chars allowed
        ):
            self.assertIsNone(
                _validate_data_key(k, tool_name="t"),
                f"{k!r} unexpectedly rejected",
            )

    def test_pre_existing_rejections_still_work(self):
        # Pre-PR-#109 rejection rules must still trigger with their
        # specific error messages (not the new generic URL-safety message).
        cases = [
            ("", "non-empty"),
            ("internal_x", "server-protected"),
            ("../escape", None),  # path traversal — checked but message text varies
            ("foo/bar", "not contain"),
            ("foo?q", "not contain"),
            ("foo#h", "not contain"),
        ]
        for bad, expected_substr in cases:
            err = _validate_data_key(bad, tool_name="t")
            self.assertIsNotNone(err, f"{bad!r} should still be rejected")
            if expected_substr:
                self.assertIn(
                    expected_substr,
                    err,
                    f"{bad!r} expected to mention {expected_substr!r}, got: {err}",
                )

    def test_url_safety_message_includes_pattern_hint(self):
        err = _validate_data_key("hex color", tool_name="my_tool")
        self.assertIsNotNone(err)
        self.assertIn("my_tool", err)
        # Message should hint at allowed characters or URL-safety
        self.assertTrue(
            any(s in err.lower() for s in ("url", "letters", "allowed", "safe", "match")),
            f"error message should explain allowed characters: {err}",
        )

    def test_url_safety_message_shows_decoded_form_when_different(self):
        # PR #110 review nit: when the raw key differs from its decoded
        # form (e.g. percent-encoded space), echo both so the caller can
        # see the actual offending character.
        err = _validate_data_key("hex%20color", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("hex%20color", err)  # raw
        self.assertIn("hex color", err)  # decoded
        self.assertIn("decodes to", err)

    def test_url_safety_message_omits_decoded_form_when_same(self):
        # When the raw key has no encoding to unwind, the message should
        # not bloat with a duplicated "decodes to" clause.
        err = _validate_data_key("hex color", tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("hex color", err)
        self.assertNotIn("decodes to", err)


if __name__ == "__main__":
    unittest.main()
