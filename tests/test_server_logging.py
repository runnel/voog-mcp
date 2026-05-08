"""Tests for _redact_arguments helper and the DEBUG log call in server.py (T7).

Verifies that:
- standard non-sensitive arguments pass through unchanged
- known content-bearing keys (body, data, value, source) are redacted
- long string values for any key are length-capped
- the original dict is never mutated
- empty / non-dict inputs are handled gracefully
- the actual call_tool DEBUG log path does not leak body content
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest.mock import MagicMock, patch

from voog.mcp.server import _REDACTED_KEYS, _STRING_CAP, _redact_arguments


class TestRedactArgumentsPassthrough(unittest.TestCase):
    """Non-sensitive fields must pass through untouched."""

    def test_standard_fields_unchanged(self):
        args = {"site": "x", "page_id": 5, "force": True}
        result = _redact_arguments(args)
        self.assertEqual(result, {"site": "x", "page_id": 5, "force": True})

    def test_empty_dict(self):
        self.assertEqual(_redact_arguments({}), {})

    def test_site_key_not_redacted(self):
        result = _redact_arguments({"site": "stellasoomlais"})
        self.assertEqual(result["site"], "stellasoomlais")

    def test_force_bool_not_redacted(self):
        result = _redact_arguments({"force": False})
        self.assertEqual(result["force"], False)

    def test_integer_value_passes_through(self):
        result = _redact_arguments({"page_id": 42})
        self.assertEqual(result["page_id"], 42)

    def test_list_value_passes_through(self):
        result = _redact_arguments({"ids": [1, 2, 3]})
        self.assertEqual(result["ids"], [1, 2, 3])


class TestRedactArgumentsSensitiveKeys(unittest.TestCase):
    """Each key in _REDACTED_KEYS must produce '<redacted>' regardless of value."""

    def test_body_redacted(self):
        result = _redact_arguments({"body": "secret content"})
        self.assertEqual(result["body"], "<redacted>")

    def test_data_redacted(self):
        result = _redact_arguments({"data": {"foo": "bar"}})
        self.assertEqual(result["data"], "<redacted>")

    def test_value_redacted(self):
        result = _redact_arguments({"value": "some text value"})
        self.assertEqual(result["value"], "<redacted>")

    def test_source_redacted(self):
        result = _redact_arguments({"source": "<html>...</html>"})
        self.assertEqual(result["source"], "<redacted>")

    def test_all_redacted_keys_covered(self):
        """Every key in _REDACTED_KEYS must produce '<redacted>'."""
        for key in _REDACTED_KEYS:
            with self.subTest(key=key):
                result = _redact_arguments({key: "anything"})
                self.assertEqual(result[key], "<redacted>", f"key '{key}' was not redacted")

    def test_empty_string_body_still_redacted(self):
        # An empty string body is still a content-bearing field
        result = _redact_arguments({"body": ""})
        self.assertEqual(result["body"], "<redacted>")


class TestRedactArgumentsLongStringCap(unittest.TestCase):
    """Strings longer than _STRING_CAP must be truncated with placeholder."""

    def test_long_title_capped(self):
        long_val = "a" * 1000
        result = _redact_arguments({"title": long_val})
        self.assertNotEqual(result["title"], long_val)
        self.assertIn("1000", result["title"])
        self.assertIn("truncated", result["title"])

    def test_short_title_not_capped(self):
        short_val = "a" * 10
        result = _redact_arguments({"title": short_val})
        self.assertEqual(result["title"], short_val)

    def test_string_exactly_at_cap_not_truncated(self):
        val = "x" * _STRING_CAP
        result = _redact_arguments({"name": val})
        self.assertEqual(result["name"], val)

    def test_string_one_over_cap_is_truncated(self):
        val = "x" * (_STRING_CAP + 1)
        result = _redact_arguments({"name": val})
        self.assertIn("truncated", result["name"])
        self.assertIn(str(_STRING_CAP + 1), result["name"])

    def test_long_string_placeholder_format(self):
        result = _redact_arguments({"name": "z" * 600})
        self.assertEqual(result["name"], "<truncated, 600 chars>")


class TestRedactArgumentsMixed(unittest.TestCase):
    """Redaction + cap + passthrough must all work correctly in a single call."""

    def test_mixed_dict(self):
        args = {
            "site": "mysite",        # passthrough (identifier)
            "page_id": 7,            # passthrough (int)
            "body": "some html",     # redacted key
            "title": "t" * 800,     # long string → capped
            "force": True,           # passthrough (bool)
        }
        result = _redact_arguments(args)
        self.assertEqual(result["site"], "mysite")
        self.assertEqual(result["page_id"], 7)
        self.assertEqual(result["body"], "<redacted>")
        self.assertEqual(result["title"], "<truncated, 800 chars>")
        self.assertEqual(result["force"], True)

    def test_redacted_key_with_long_value_still_just_redacted(self):
        # Redaction takes priority over the length cap for keys in _REDACTED_KEYS
        result = _redact_arguments({"body": "x" * 2000})
        self.assertEqual(result["body"], "<redacted>")


class TestRedactArgumentsDoesNotMutate(unittest.TestCase):
    """The original dict must be unchanged after calling _redact_arguments."""

    def test_sensitive_key_not_mutated(self):
        original = {"body": "secret", "site": "s"}
        original_copy = dict(original)
        _redact_arguments(original)
        self.assertEqual(original, original_copy)

    def test_long_value_not_mutated(self):
        long_val = "a" * 1000
        original = {"title": long_val}
        _redact_arguments(original)
        self.assertEqual(original["title"], long_val)

    def test_normal_dict_not_mutated(self):
        original = {"site": "x", "page_id": 5}
        _redact_arguments(original)
        self.assertEqual(original, {"site": "x", "page_id": 5})


class TestRedactArgumentsEdgeCases(unittest.TestCase):
    """Defensive handling of unexpected input types."""

    def test_none_input_returns_empty_dict(self):
        self.assertEqual(_redact_arguments(None), {})  # type: ignore[arg-type]

    def test_string_input_returns_empty_dict(self):
        self.assertEqual(_redact_arguments("not a dict"), {})  # type: ignore[arg-type]

    def test_list_input_returns_empty_dict(self):
        self.assertEqual(_redact_arguments([1, 2, 3]), {})  # type: ignore[arg-type]

    def test_nested_dict_in_non_redacted_key_passes_through(self):
        # Nested dicts in non-sensitive keys are not recursed into
        result = _redact_arguments({"options": {"key": "value"}})
        self.assertEqual(result["options"], {"key": "value"})

    def test_nested_dict_in_redacted_key_is_just_redacted(self):
        # The value of "data" is replaced regardless of its structure
        result = _redact_arguments({"data": {"foo": "bar", "nested": {"x": 1}}})
        self.assertEqual(result["data"], "<redacted>")


class TestCallToolDebugLogDoesNotLeakBody(unittest.TestCase):
    """Integration: the DEBUG log emitted by handle_call_tool must not
    contain the raw value of content-bearing fields like 'body'.

    This is the key regression-pin test for the T7 fix.
    """

    def _capture_handlers(self):
        """Reuse the same handler-capture pattern as test_server_async_dispatch."""
        from voog.config import GlobalConfig, SiteConfig
        from voog.mcp import server as server_module

        captured: dict = {}
        fake_server = MagicMock()

        def _decorator_factory(slot: str):
            def _decorator():
                def _wrap(fn):
                    captured[slot] = fn
                    return fn
                return _wrap
            return _decorator

        fake_server.list_tools = _decorator_factory("list_tools")
        fake_server.call_tool = _decorator_factory("call_tool")
        fake_server.list_resources = _decorator_factory("list_resources")
        fake_server.read_resource = _decorator_factory("read_resource")

        class _StdioCancel:
            async def __aenter__(self):
                raise asyncio.CancelledError("stop here")
            async def __aexit__(self, *exc):
                return False

        global_cfg = GlobalConfig(
            sites={"test": SiteConfig(name="test", host="example.com", api_key_env="TEST_API_TOKEN")},
            default_site="test",
        )
        env = {"TEST_API_TOKEN": "dummy-token"}

        with (
            patch.object(server_module, "Server", return_value=fake_server),
            patch.object(server_module, "VoogClient"),
            patch.object(server_module, "stdio_server", return_value=_StdioCancel()),
        ):
            try:
                asyncio.run(server_module.run_server(global_cfg, env))
            except asyncio.CancelledError:
                pass
        return captured

    def test_debug_log_does_not_contain_body_content(self):
        """The DEBUG log message must not expose the raw body string."""
        from voog.mcp import server as server_module
        from voog.mcp.tools import pages as pages_tools

        handlers = self._capture_handlers()
        handle_call_tool = handlers["call_tool"]

        secret_body = "THIS IS SECRET CONTENT THAT MUST NOT APPEAR IN LOGS"

        # Patch the tool group so the call returns without hitting the network
        def fake_call_tool(name, arguments, client):
            return [{"type": "text", "text": "ok"}]

        with (
            patch.object(pages_tools, "call_tool", side_effect=fake_call_tool),
            self.assertLogs("voog", level=logging.DEBUG) as log_ctx,
        ):
            asyncio.run(
                handle_call_tool(
                    "pages_list",
                    {"site": "test", "body": secret_body},
                )
            )

        combined_output = "\n".join(log_ctx.output)
        self.assertNotIn(
            secret_body,
            combined_output,
            "Secret body content must not appear in DEBUG log output",
        )
        # But the tool name must still be present so the log is useful
        self.assertIn("pages_list", combined_output)


if __name__ == "__main__":
    unittest.main()
