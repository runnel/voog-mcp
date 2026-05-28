"""Unit tests for voog.client redaction helpers (S-1, S-9)."""

import logging
import unittest
from unittest.mock import patch

import httpx

from voog.client import (
    _QUERY_STRING_VALUE_CAP,
    _SENSITIVE_HEADERS,
    VoogClient,
    _redact_headers,
    _redact_query_string,
)


def _make_httpx_response(status_code=200, content=b'{"k":1}'):
    # raise_for_status requires a Request bound to the Response. The
    # actual URL of the Request is irrelevant — _request only consults
    # the Response status code and body.
    req = httpx.Request("GET", "https://example.com/admin/api/pages")
    return httpx.Response(status_code=status_code, content=content, request=req)


class TestSensitiveHeaderSet(unittest.TestCase):
    def test_contains_known_sensitive_names(self):
        # Lower-case canonical forms only — _redact_headers does the
        # case-folding at lookup time.
        self.assertIn("x-api-token", _SENSITIVE_HEADERS)
        self.assertIn("authorization", _SENSITIVE_HEADERS)
        self.assertIn("cookie", _SENSITIVE_HEADERS)

    def test_does_not_contain_non_sensitive(self):
        self.assertNotIn("user-agent", _SENSITIVE_HEADERS)
        self.assertNotIn("content-type", _SENSITIVE_HEADERS)
        self.assertNotIn("accept", _SENSITIVE_HEADERS)


class TestRedactHeaders(unittest.TestCase):
    def test_x_api_token_redacted(self):
        out = _redact_headers({"X-API-Token": "abc123secret"})
        self.assertEqual(out["X-API-Token"], "***")

    def test_authorization_redacted(self):
        out = _redact_headers({"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9"})
        self.assertEqual(out["Authorization"], "***")

    def test_cookie_redacted(self):
        out = _redact_headers({"Cookie": "session=abc; csrf=def"})
        self.assertEqual(out["Cookie"], "***")

    def test_case_insensitive_match(self):
        # HTTP header names are case-insensitive per RFC 7230.
        out = _redact_headers({"x-api-token": "abc", "AUTHORIZATION": "Bearer xyz"})
        self.assertEqual(out["x-api-token"], "***")
        self.assertEqual(out["AUTHORIZATION"], "***")

    def test_non_sensitive_pass_through(self):
        out = _redact_headers(
            {
                "User-Agent": "voog-mcp/1.4",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        self.assertEqual(out["User-Agent"], "voog-mcp/1.4")
        self.assertEqual(out["Content-Type"], "application/json")
        self.assertEqual(out["Accept"], "application/json")

    def test_mixed_dict(self):
        inp = {
            "X-API-Token": "secret",
            "User-Agent": "voog-mcp/1.4",
            "Cookie": "session=abc",
        }
        out = _redact_headers(inp)
        self.assertEqual(out["X-API-Token"], "***")
        self.assertEqual(out["User-Agent"], "voog-mcp/1.4")
        self.assertEqual(out["Cookie"], "***")

    def test_input_not_mutated(self):
        inp = {"X-API-Token": "secret", "User-Agent": "voog-mcp/1.4"}
        _redact_headers(inp)
        # Mutation would smuggle "***" back into the live request dict.
        self.assertEqual(inp["X-API-Token"], "secret")

    def test_empty_dict(self):
        self.assertEqual(_redact_headers({}), {})


class TestRedactQueryString(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(_redact_query_string(""), "")

    def test_short_value_pass_through(self):
        # `per_page=250` is well under the cap; useful debugging context.
        self.assertEqual(_redact_query_string("per_page=250"), "per_page=250")

    def test_short_value_at_cap_boundary_pass_through(self):
        # Exactly ``_QUERY_STRING_VALUE_CAP`` characters — NOT redacted
        # (the rule is "longer than", not "at least").
        v = "a" * _QUERY_STRING_VALUE_CAP
        self.assertEqual(_redact_query_string(f"k={v}"), f"k={v}")

    def test_long_value_redacted(self):
        long_sig = "a" * 256  # mirrors X-Amz-Signature
        out = _redact_query_string(f"X-Amz-Signature={long_sig}")
        self.assertEqual(out, "X-Amz-Signature=***256-chars***")

    def test_key_retained(self):
        # The key is the load-bearing debugging context — must not be
        # redacted along with the value.
        out = _redact_query_string(f"api_key={'x' * 100}")
        self.assertTrue(out.startswith("api_key=***"))
        self.assertIn("100-chars", out)

    def test_mixed_short_and_long(self):
        long_val = "b" * 80
        out = _redact_query_string(f"per_page=250&signature={long_val}&page=1")
        self.assertEqual(out, "per_page=250&signature=***80-chars***&page=1")

    def test_pair_without_equals_pass_through(self):
        # Defensive: malformed query string segments preserved verbatim.
        self.assertEqual(_redact_query_string("flag&k=v"), "flag&k=v")

    def test_repeated_key_each_redacted_independently(self):
        long_a = "a" * 60
        long_b = "b" * 60
        out = _redact_query_string(f"tag={long_a}&tag={long_b}")
        self.assertEqual(out, "tag=***60-chars***&tag=***60-chars***")


class TestRequestLogIncludesRedactedParams(unittest.TestCase):
    """S-9 integration: typed-tool calls pass ``params=`` as a separate
    kwarg; the DEBUG log line must show the merged query string (with
    long values redacted) so the redactor is load-bearing — not just a
    helper that protects the rare passthrough path.
    """

    def _captured_debug_lines(self, log_records: list[logging.LogRecord]) -> list[str]:
        return [r.getMessage() for r in log_records if r.levelno == logging.DEBUG]

    def test_params_appear_in_debug_log(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with self.assertLogs("voog.client", level="DEBUG") as logs:
                client.get("/pages", params={"per_page": 250, "include": "variants"})
        lines = self._captured_debug_lines(logs.records)
        # At least one DEBUG line contains the merged query string —
        # before the fix this was logged as just "GET /pages" with no
        # query string at all.
        joined = "\n".join(lines)
        self.assertIn("per_page=250", joined)
        self.assertIn("include=variants", joined)

    def test_long_param_value_redacted_in_debug_log(self):
        client = VoogClient(host="example.com", api_token="t")
        long_val = "a" * 100  # over cap=50
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with self.assertLogs("voog.client", level="DEBUG") as logs:
                client.get("/pages", params={"signature": long_val})
        joined = "\n".join(self._captured_debug_lines(logs.records))
        # The raw 100-char value MUST NOT appear; the redacted placeholder MUST.
        self.assertNotIn(long_val, joined)
        self.assertIn("signature=***100-chars***", joined)

    def test_no_params_logs_bare_url(self):
        # Regression: when no params are passed, the URL is logged without
        # a trailing "?" — keeps log lines clean for the common case.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with self.assertLogs("voog.client", level="DEBUG") as logs:
                client.get("/pages")
        joined = "\n".join(self._captured_debug_lines(logs.records))
        self.assertIn("/pages", joined)
        self.assertNotIn("/pages?", joined)

    def test_query_string_in_path_still_redacted(self):
        # Passthrough path: caller embeds `?` in path directly. The
        # original (pre-Finding-1) behaviour. Still works.
        client = VoogClient(host="example.com", api_token="t")
        long_sig = "x" * 200
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with self.assertLogs("voog.client", level="DEBUG") as logs:
                client.get(f"/pages?X-Amz-Signature={long_sig}")
        joined = "\n".join(self._captured_debug_lines(logs.records))
        self.assertNotIn(long_sig, joined)
        self.assertIn("X-Amz-Signature=***200-chars***", joined)


if __name__ == "__main__":
    unittest.main()
