"""Unit tests for voog.client redaction helpers (S-1, S-9)."""

import unittest

from voog.client import _SENSITIVE_HEADERS, _redact_headers


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


if __name__ == "__main__":
    unittest.main()
