"""Tests for VoogClient request-budget + quota wiring (S-6, S-7)."""

import os
import unittest
from unittest.mock import patch

import httpx

from tests.test_quota import _TmpQuotaPath  # reuse fixture
from voog.client import (
    _DEFAULT_REQUEST_CAP,
    _REQUEST_BUDGET_WARN_AT,
    VoogClient,
    _resolve_request_cap,
)
from voog.errors import DailyQuotaExceeded, RequestBudgetExceeded


def _make_httpx_response(status_code: int = 200, body: bytes = b'{"ok": true}') -> httpx.Response:
    req = httpx.Request("GET", "https://example.com/admin/api/pages")
    return httpx.Response(status_code=status_code, content=body, request=req)


def _patch_request(client, *, responses=None, side_effect=None):
    """Patch the client's httpx request method. Either pass *responses*
    (list/iterable of Response objects to return in order) or
    *side_effect* (callable raising/returning per call)."""
    if responses is not None:
        iterator = iter(responses)
        return patch.object(
            client._http_client, "request", side_effect=lambda *a, **k: next(iterator)
        )
    return patch.object(client._http_client, "request", side_effect=side_effect)


class TestResolveRequestCap(unittest.TestCase):
    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOOG_REQUEST_CAP", None)
            self.assertEqual(_resolve_request_cap(), _DEFAULT_REQUEST_CAP)

    def test_zero_disables_cap(self):
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": "0"}, clear=False):
            self.assertIsNone(_resolve_request_cap())

    def test_positive_value(self):
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": "42"}, clear=False):
            self.assertEqual(_resolve_request_cap(), 42)

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": "-5"}, clear=False):
            with self.assertLogs("voog.client", level="WARNING"):
                self.assertEqual(_resolve_request_cap(), _DEFAULT_REQUEST_CAP)

    def test_non_integer_falls_back_to_default(self):
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": "notanumber"}, clear=False):
            with self.assertLogs("voog.client", level="WARNING"):
                self.assertEqual(_resolve_request_cap(), _DEFAULT_REQUEST_CAP)


class TestRequestCountIncrements(unittest.TestCase):
    def test_counter_starts_at_zero(self):
        c = VoogClient(host="example.com", api_token="t")
        self.assertEqual(c._request_count, 0)

    def test_counter_increments_per_successful_request(self):
        c = VoogClient(host="example.com", api_token="t")
        responses = [_make_httpx_response(body=b'{"ok":%d}' % i) for i in range(3)]
        with _patch_request(c, responses=responses):
            c.get("/pages")
            c.get("/pages")
            c.get("/pages")
        self.assertEqual(c._request_count, 3)

    def test_retries_count_as_one_request_R8(self):
        """R8: retries inside _request count as one logical request."""
        c = VoogClient(host="example.com", api_token="t", max_retries=2)
        # First two attempts 503, third attempt succeeds. _request retries
        # on 5xx by raising httpx.HTTPStatusError; we synthesise that
        # behaviour via responses with status_code=503 (raise_for_status
        # surfaces them) and then a 200.
        responses = [
            _make_httpx_response(status_code=503, body=b'{"e":"down"}'),
            _make_httpx_response(status_code=503, body=b'{"e":"down"}'),
            _make_httpx_response(status_code=200, body=b'{"ok":true}'),
        ]
        with _patch_request(c, responses=responses):
            with patch("voog.client.time.sleep"):
                c.get("/pages")
        # Three HTTP attempts under the hood; one logical request from
        # the caller's perspective per R8.
        self.assertEqual(c._request_count, 1)

    def test_failed_request_does_not_increment(self):
        """Per R8 docstring: count successful requests only — a 4xx
        terminal failure should NOT bump the counter."""
        c = VoogClient(host="example.com", api_token="t")
        with _patch_request(c, responses=[_make_httpx_response(status_code=404, body=b"{}")]):
            with self.assertRaises(httpx.HTTPStatusError):
                c.get("/pages/9999")
        self.assertEqual(c._request_count, 0)


class TestBudgetCap(unittest.TestCase):
    def test_raises_when_count_exceeds_cap(self):
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": "3"}, clear=False):
            c = VoogClient(host="example.com", api_token="t")
            responses = [_make_httpx_response() for _ in range(5)]
            with _patch_request(c, responses=responses):
                c.get("/pages")  # 1
                c.get("/pages")  # 2
                c.get("/pages")  # 3
                with self.assertRaises(RequestBudgetExceeded) as ctx:
                    c.get("/pages")  # 4 — over cap
            self.assertIn("cap=3", str(ctx.exception))
            self.assertIn("count=4", str(ctx.exception))

    def test_warning_at_threshold(self):
        # Use the constant directly so the test moves with the threshold.
        warn_at = _REQUEST_BUDGET_WARN_AT
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": str(warn_at + 10)}, clear=False):
            c = VoogClient(host="example.com", api_token="t")
            responses = [_make_httpx_response() for _ in range(warn_at)]
            with _patch_request(c, responses=responses):
                with self.assertLogs("voog.client", level="WARNING") as logs:
                    for _ in range(warn_at):
                        c.get("/pages")
            # At least one WARNING line mentioning the threshold.
            self.assertTrue(
                any(f"reached {warn_at}" in line for line in logs.output),
                f"expected warning at {warn_at}, got {logs.output}",
            )

    def test_warning_fires_only_once(self):
        warn_at = _REQUEST_BUDGET_WARN_AT
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": str(warn_at + 10)}, clear=False):
            c = VoogClient(host="example.com", api_token="t")
            responses = [_make_httpx_response() for _ in range(warn_at + 5)]
            with _patch_request(c, responses=responses):
                with self.assertLogs("voog.client", level="WARNING") as logs:
                    for _ in range(warn_at + 5):
                        c.get("/pages")
            warn_lines = [ln for ln in logs.output if "reached " in ln]
            self.assertEqual(len(warn_lines), 1)

    def test_warning_fires_if_count_skips_past_threshold(self):
        """Lost-update race: under concurrent fan-out, ``_request_count``
        can skip from 999 directly to 1001 (two workers both reading 999,
        both writing 1000 — one lost update, then a third worker takes
        it to 1001 instead of the expected 1000). The warn check must
        still fire — guards via ``>=`` rather than ``==``.
        """
        warn_at = _REQUEST_BUDGET_WARN_AT
        with patch.dict(os.environ, {"VOOG_REQUEST_CAP": str(warn_at + 10)}, clear=False):
            c = VoogClient(host="example.com", api_token="t")
            # Simulate the lost-update outcome directly: pre-seed count to
            # warn_at-1, then one successful request takes it to warn_at,
            # but we also pre-set it to warn_at+1 to verify the threshold
            # is treated as "≥".
            c._request_count = warn_at + 1  # already past the boundary
            with _patch_request(c, responses=[_make_httpx_response()]):
                with self.assertLogs("voog.client", level="WARNING") as logs:
                    c.get("/pages")
            warn_lines = [ln for ln in logs.output if "reached" in ln]
            self.assertEqual(len(warn_lines), 1, f"expected one warn line, got {logs.output}")


class TestQuotaIntegration(unittest.TestCase):
    def test_no_site_name_no_quota_calls(self):
        # Without site_name, the quota path is skipped entirely.
        c = VoogClient(host="example.com", api_token="t")
        responses = [_make_httpx_response() for _ in range(3)]
        with _TmpQuotaPath() as path:
            with _patch_request(c, responses=responses):
                c.get("/pages")
                c.get("/pages")
                c.get("/pages")
            # No quota file should have been written.
            self.assertFalse(path.exists())

    def test_site_name_no_quota_limit_skips_disk_write(self):
        c = VoogClient(
            host="example.com",
            api_token="t",
            site_name="stella",
            daily_request_quota=None,
        )
        responses = [_make_httpx_response() for _ in range(3)]
        with _TmpQuotaPath():
            with _patch_request(c, responses=responses):
                c.get("/pages")
                c.get("/pages")
                c.get("/pages")
            # daily_request_quota=None ⇒ no quota call at all (avoids the
            # disk write entirely when the operator opted out).
            from voog import quota as _quota

            self.assertEqual(_quota.read_count("stella"), 0)

    def test_site_with_limit_raises_when_exceeded(self):
        c = VoogClient(
            host="example.com",
            api_token="t",
            site_name="stella",
            daily_request_quota=2,
        )
        responses = [_make_httpx_response() for _ in range(3)]
        with _TmpQuotaPath():
            with _patch_request(c, responses=responses):
                c.get("/pages")  # 1
                c.get("/pages")  # 2
                with self.assertRaises(DailyQuotaExceeded):
                    c.get("/pages")  # 3 — over


if __name__ == "__main__":
    unittest.main()
