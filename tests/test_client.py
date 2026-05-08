"""Unit tests for VoogClient."""

import socket
import unittest
import urllib.error
from http.client import HTTPMessage
from unittest.mock import MagicMock, patch

from voog.client import VoogClient, _parse_retry_after


class TestVoogClient(unittest.TestCase):
    def test_init_sets_base_urls(self):
        client = VoogClient(host="example.com", api_token="testtoken")
        self.assertEqual(client.base_url, "https://example.com/admin/api")
        self.assertEqual(client.ecommerce_url, "https://example.com/admin/api/ecommerce/v1")

    def test_init_sets_headers(self):
        client = VoogClient(host="example.com", api_token="testtoken")
        self.assertEqual(client.headers["X-API-Token"], "testtoken")
        self.assertEqual(client.headers["Content-Type"], "application/json")


class TestVoogClientTimeout(unittest.TestCase):
    """HTTP timeout — long-running MCP server cannot afford to hang on a stuck
    connection (a hung HTTP call wedges the entire Claude session)."""

    def test_default_timeout_is_60_seconds(self):
        client = VoogClient(host="example.com", api_token="t")
        self.assertEqual(client.timeout, 60)

    def test_custom_timeout_stored(self):
        client = VoogClient(host="example.com", api_token="t", timeout=15)
        self.assertEqual(client.timeout, 15)

    def test_request_passes_timeout_to_urlopen(self):
        client = VoogClient(host="example.com", api_token="t", timeout=15)
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.get("/pages")
        _, kwargs = mock_urlopen.call_args
        self.assertEqual(kwargs.get("timeout"), 15)

    def test_request_uses_default_timeout_when_not_overridden(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.get("/pages")
        _, kwargs = mock_urlopen.call_args
        self.assertEqual(kwargs.get("timeout"), 60)


class TestRequestUrlEncoding(unittest.TestCase):
    """Querystring assembly uses urllib.parse.urlencode — encodes both keys
    and values. Old hand-rolled code only quoted values; if a non-alphanumeric
    key ever appears (none today, but no longer a footgun) it would break the
    URL. Documenting the fix with a direct test on `_request`.
    """

    def test_request_urlencodes_keys_with_special_chars(self):
        client = VoogClient(host="x.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.get("/x", params={"foo bar": "y"})
        url = mock_urlopen.call_args.args[0].full_url
        # urlencode encodes spaces in keys as '+' (form-encoded)
        self.assertIn("foo+bar=y", url)

    def test_request_urlencodes_values_with_special_chars(self):
        client = VoogClient(host="x.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.get("/x", params={"include": "variant_types,translations"})
        url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("include=variant_types%2Ctranslations", url)


class TestPatchMethod(unittest.TestCase):
    """VoogClient.patch() is a public helper that mirrors put/post/delete."""

    def test_patch_sends_correct_method_and_body(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b'{"id": 5}'
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            result = client.patch("/site", {"title": "X"})
        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertEqual(result, {"id": 5})

    def test_patch_with_custom_base(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.patch("/settings", {"x": 1}, base="https://example.com/admin/api/ecommerce/v1")
        url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("ecommerce/v1/settings", url)


class TestGetAllParamsPassthrough(unittest.TestCase):
    """get_all merges caller params with pagination params."""

    def _make_client(self):
        client = VoogClient(host="example.com", api_token="t")
        # Mock the underlying single-page get to avoid real HTTP
        client.get = MagicMock(return_value=[])  # one empty page → loop exits
        return client

    def test_no_params_uses_only_pagination(self):
        client = self._make_client()
        client.get_all("/pages")
        client.get.assert_called_once_with("/pages", base=None, params={"per_page": 200, "page": 1})

    def test_caller_params_merged_with_pagination(self):
        client = self._make_client()
        client.get_all("/products", params={"include": "translations"})
        client.get.assert_called_once_with(
            "/products",
            base=None,
            params={"per_page": 200, "page": 1, "include": "translations"},
        )

    def test_base_kwarg_passed_through(self):
        client = self._make_client()
        client.get_all("/products", base=client.ecommerce_url, params={"include": "x"})
        args, kwargs = client.get.call_args
        self.assertEqual(kwargs["base"], client.ecommerce_url)

    def test_caller_can_override_per_page(self):
        # `per_page` is overridable — escape hatch for endpoints that
        # benefit from a different page size.
        client = self._make_client()
        client.get_all("/x", params={"per_page": 50})
        args, kwargs = client.get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 50)

    def test_caller_page_param_ignored(self):
        # CRITICAL: caller-supplied `page` MUST NOT win — overriding it
        # would silently re-fetch the same page every iteration and
        # infinite-loop on endpoints with ≥1 full page. The iteration
        # counter always wins.
        client = VoogClient(host="example.com", api_token="t")
        client.get = MagicMock(
            side_effect=[
                [{"id": i} for i in range(200)],  # page 1, full (new default 200)
                [{"id": 200}],  # page 2, partial → loop exits
            ]
        )
        client.get_all("/x", params={"page": 99})  # caller's `page` ignored
        first_call = client.get.call_args_list[0]
        second_call = client.get.call_args_list[1]
        # Iteration counter wins on both calls, regardless of caller's page=99
        self.assertEqual(first_call.kwargs["params"]["page"], 1)
        self.assertEqual(second_call.kwargs["params"]["page"], 2)

    def test_pagination_increments_page_across_calls(self):
        client = VoogClient(host="example.com", api_token="t")
        # First page returns full 200 (new default), second returns partial → loop exits
        client.get = MagicMock(
            side_effect=[
                [{"id": i} for i in range(200)],
                [{"id": 200}],
            ]
        )
        result = client.get_all("/x", params={"include": "y"})
        self.assertEqual(len(result), 201)
        # Page 1 and 2 both got the include param
        first_call = client.get.call_args_list[0]
        second_call = client.get.call_args_list[1]
        self.assertEqual(first_call.kwargs["params"]["page"], 1)
        self.assertEqual(first_call.kwargs["params"]["include"], "y")
        self.assertEqual(second_call.kwargs["params"]["page"], 2)
        self.assertEqual(second_call.kwargs["params"]["include"], "y")


class TestGetAllPagination(unittest.TestCase):
    """Regression guards for B3 (audit 03-bugs-and-correctness.md)."""

    def test_terminates_when_page_short_under_caller_per_page(self):
        # Caller asks for per_page=250. Page 1 returns 150 items. The
        # short page (<250) means iteration must stop — but the pre-fix
        # implementation hardcoded `< 100`, which would loop forever
        # (or until Voog's empty page) on a real 150-item endpoint.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = [{"id": i} for i in range(150)]
            results = client.get_all("/pages", params={"per_page": 250})
        self.assertEqual(len(results), 150)
        # Exactly one request was made — termination on the short page.
        mock_get.assert_called_once()

    def test_terminates_when_caller_per_page_full_page_then_empty(self):
        # Caller asks for per_page=200. Page 1 returns 200 (full); page 2
        # returns 0. Pre-fix code never terminated correctly here unless
        # the empty-page short-circuit caught it.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.side_effect = [
                [{"id": i} for i in range(200)],
                [],
            ]
            results = client.get_all("/pages", params={"per_page": 200})
        self.assertEqual(len(results), 200)
        self.assertEqual(mock_get.call_count, 2)

    def test_terminates_on_empty_first_page(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = []
            results = client.get_all("/pages")
        self.assertEqual(results, [])
        mock_get.assert_called_once()

    def test_default_per_page_is_200(self):
        # I10: default per_page raised to 200 (audit P2 — was 100).
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = []
            client.get_all("/pages")
        # The first (and only, since [] returns) call's params should
        # include per_page=200.
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 200)

    def test_caller_per_page_override_wins(self):
        # Regression guard: caller-supplied per_page in params still
        # overrides the default.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = []
            client.get_all("/pages", params={"per_page": 250})
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 250)


class TestRequestRetry(unittest.TestCase):
    """Audit I9 — retry on transient failures (5xx, OSError)."""

    def test_5xx_is_retried_then_succeeds(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b'{"ok": true}'
        err_503 = urllib.error.HTTPError(
            url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None
        )
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [err_503, cm]
            with patch("voog.client.time.sleep") as mock_sleep:
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 2)
        # Backoff: 0.5 * 2^0 = 0.5 on the first retry.
        mock_sleep.assert_called_once_with(0.5)

    def test_500_then_500_then_success(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        err_500 = urllib.error.HTTPError(url="x", code=500, msg="Internal", hdrs=None, fp=None)
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [err_500, err_500, cm]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        self.assertEqual(mock_urlopen.call_count, 3)
        # Backoff escalates: 0.5, 1.0
        self.assertEqual(
            [c.args[0] for c in mock_sleep.call_args_list],
            [0.5, 1.0],
        )

    def test_5xx_exhausted_raises(self):
        client = VoogClient(host="example.com", api_token="t")
        err_502 = urllib.error.HTTPError(url="x", code=502, msg="Bad Gateway", hdrs=None, fp=None)
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_502, err_502, err_502]
            with patch("voog.client.time.sleep"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    client.get("/pages")
        self.assertEqual(ctx.exception.code, 502)
        # 3 total attempts: 1 initial + 2 retries.
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_4xx_not_retried(self):
        # 422 (validation) is a caller error — retrying spams Voog.
        client = VoogClient(host="example.com", api_token="t")
        err_422 = urllib.error.HTTPError(url="x", code=422, msg="Unprocessable", hdrs=None, fp=None)
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_422]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(urllib.error.HTTPError):
                    client.get("/pages")
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_oserror_is_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [
                OSError("connection reset by peer"),
                cm,
            ]
            with patch("voog.client.time.sleep"):
                client.get("/pages")
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_oserror_exhausted_raises(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("permanent disconnect")
            with patch("voog.client.time.sleep"):
                with self.assertRaises(OSError):
                    client.get("/pages")
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_successful_first_try_no_retry(self):
        # Regression guard: happy path makes exactly one HTTP call.
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.return_value = cm
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()


class TestRequestRetryMethodGating(unittest.TestCase):
    """PR #110 review fix: POST/PATCH must NOT retry — duplicate-resource risk."""

    def test_post_5xx_not_retried(self):
        # Voog accepted the POST, response was lost on the wire.
        # Retrying would create a SECOND product/redirect/etc.
        client = VoogClient(host="example.com", api_token="t")
        err_503 = urllib.error.HTTPError(
            url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None
        )
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_503]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    client.post("/products", {"product": {}})
        self.assertEqual(ctx.exception.code, 503)
        # Single attempt — no retry.
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_post_oserror_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [OSError("connection reset")]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(OSError):
                    client.post("/products", {"product": {}})
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_patch_5xx_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_502 = urllib.error.HTTPError(url="x", code=502, msg="Bad Gateway", hdrs=None, fp=None)
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_502]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(urllib.error.HTTPError):
                    client.patch("/resource", {})
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_put_5xx_still_retries(self):
        # Regression guard: PUT is idempotent in Voog's API (full-replace),
        # so retries are still safe and active.
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        err_503 = urllib.error.HTTPError(
            url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None
        )
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [err_503, cm]
            with patch("voog.client.time.sleep"):
                client.put("/resource", {"key": "v"})
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_delete_5xx_still_retries(self):
        # DELETE is semantically idempotent (404 on retry is fine — gone).
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b""
        err_500 = urllib.error.HTTPError(url="x", code=500, msg="Internal", hdrs=None, fp=None)
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [err_500, cm]
            with patch("voog.client.time.sleep"):
                client.delete("/pages/42")
        self.assertEqual(mock_urlopen.call_count, 2)


class TestRequestLogging(unittest.TestCase):
    """Audit I17 — _request emits debug logs for traceability."""

    def test_request_debug_logs_method_and_url(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.return_value = cm
            with self.assertLogs("voog.client", level="DEBUG") as ctx:
                client.get("/pages")
        # At least one debug record contains the method + path.
        debug_msgs = [r.getMessage() for r in ctx.records if r.levelname == "DEBUG"]
        self.assertTrue(
            any("GET" in m and "/pages" in m for m in debug_msgs),
            f"Expected GET /pages in debug logs, got: {debug_msgs}",
        )

    def test_5xx_retry_emits_warning(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        err_503 = urllib.error.HTTPError(
            url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None
        )
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [err_503, cm]
            with patch("voog.client.time.sleep"):
                with self.assertLogs("voog.client", level="WARNING") as ctx:
                    client.get("/pages")
        warning_msgs = [r.getMessage() for r in ctx.records if r.levelname == "WARNING"]
        self.assertTrue(
            any("503" in m for m in warning_msgs),
            f"Expected 503 retry warning, got: {warning_msgs}",
        )

    def test_oserror_retry_emits_warning(self):
        client = VoogClient(host="example.com", api_token="t")
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            cm = MagicMock()
            cm.__enter__.return_value = fake_resp
            cm.__exit__.return_value = False
            mock_urlopen.side_effect = [
                OSError("connection reset"),
                cm,
            ]
            with patch("voog.client.time.sleep"):
                with self.assertLogs("voog.client", level="WARNING") as ctx:
                    client.get("/pages")
        warning_msgs = [r.getMessage() for r in ctx.records if r.levelname == "WARNING"]
        self.assertTrue(
            any("connection reset" in m or "Network" in m for m in warning_msgs),
            f"Expected network error warning, got: {warning_msgs}",
        )


class TestPutPostParams(unittest.TestCase):
    """PUT /nodes/{id}/move uses query-string params per Voog docs.
    Symmetry: post also accepts params= for any future endpoint that
    needs it. Phase 6 / audit I14.
    """

    def _client(self):
        return VoogClient(host="example.com", api_token="t")

    def test_put_forwards_params(self):
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.put("/nodes/3/move", params={"parent_id": 2, "position": 1})
        url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("/nodes/3/move?", url)
        self.assertIn("parent_id=2", url)
        self.assertIn("position=1", url)

    def test_put_without_params_unchanged(self):
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.put("/articles/7", data={"autosaved_title": "x"})
        url = mock_urlopen.call_args.args[0].full_url
        self.assertNotIn("?", url)

    def test_post_forwards_params(self):
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.post("/foo", data={"x": 1}, params={"include": "bar"})
        url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("include=bar", url)

    def test_post_without_params_unchanged(self):
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = fake_resp
            client.post("/products", data={"product": {"name": "x"}})
        url = mock_urlopen.call_args.args[0].full_url
        self.assertNotIn("?", url)


def _make_http_error(code: int, msg: str, retry_after: str | None = None):
    """Build a urllib.error.HTTPError with an optional Retry-After header."""
    if retry_after is not None:
        hdrs = HTTPMessage()
        hdrs["Retry-After"] = retry_after
    else:
        hdrs = None
    return urllib.error.HTTPError(url="x", code=code, msg=msg, hdrs=hdrs, fp=None)


def _make_success_cm(body: bytes = b'{"ok": true}'):
    """Return a context-manager mock that simulates a successful response."""
    fake_resp = MagicMock()
    fake_resp.read.return_value = body
    cm = MagicMock()
    cm.__enter__.return_value = fake_resp
    cm.__exit__.return_value = False
    return cm


class TestParseRetryAfter(unittest.TestCase):
    """Unit tests for the _parse_retry_after helper."""

    def test_integer_seconds_returned_as_float(self):
        self.assertEqual(_parse_retry_after("30", fallback=0.5), 30.0)

    def test_cap_applied_to_large_values(self):
        self.assertEqual(_parse_retry_after("600", fallback=0.5), 60.0)

    def test_zero_clamped_to_minimum_one(self):
        self.assertEqual(_parse_retry_after("0", fallback=0.5), 1.0)

    def test_negative_clamped_to_minimum_one(self):
        self.assertEqual(_parse_retry_after("-5", fallback=0.5), 1.0)

    def test_invalid_string_returns_fallback(self):
        self.assertEqual(_parse_retry_after("not-a-number", fallback=2.5), 2.5)

    def test_http_date_falls_back(self):
        # HTTP-date format is not parsed — fallback is used.
        self.assertEqual(
            _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", fallback=1.0),
            1.0,
        )

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_retry_after("  15  ", fallback=0.5), 15.0)


class TestRateLimitRetry(unittest.TestCase):
    """T6 — 429 Too Many Requests retry + Retry-After header honoring."""

    def test_429_retried_then_succeeds(self):
        # 429 is now in _RETRYABLE_STATUS; a single 429 followed by 200 OK
        # should succeed without raising.
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_http_error(429, "Too Many Requests")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429, _make_success_cm()]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_429_exhausted_raises(self):
        # If we exhaust all retries on 429, the exception propagates.
        client = VoogClient(host="example.com", api_token="t", max_retries=2)
        err_429 = _make_http_error(429, "Too Many Requests")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429, err_429, err_429]
            with patch("voog.client.time.sleep"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    client.get("/pages")
        self.assertEqual(ctx.exception.code, 429)
        self.assertEqual(mock_urlopen.call_count, 3)

    def test_503_still_retried(self):
        # Regression: 503 must continue to retry after the refactor to
        # _RETRYABLE_STATUS.
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_http_error(503, "Service Unavailable")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_503, _make_success_cm()]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_retry_after_integer_honored_on_429(self):
        # When 429 has Retry-After: 5, sleep must be called with 5.0.
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_http_error(429, "Too Many Requests", retry_after="5")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(5.0)

    def test_retry_after_honored_on_503(self):
        # 503 also honors Retry-After (Cloudflare can emit it on either).
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_http_error(503, "Service Unavailable", retry_after="10")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_503, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(10.0)

    def test_retry_after_cap_at_60(self):
        # Retry-After: 600 must be capped at 60, not 600.
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_http_error(503, "Service Unavailable", retry_after="600")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_503, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(60.0)

    def test_retry_after_invalid_uses_exponential_backoff(self):
        # Unparseable Retry-After falls back to 0.5 * 2^0 = 0.5 on attempt 0.
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_http_error(429, "Too Many Requests", retry_after="not-a-number")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_no_retry_after_uses_exponential_backoff_for_429(self):
        # No Retry-After header → standard exponential backoff.
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_http_error(429, "Too Many Requests")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_5xx_no_retry_after_still_uses_exponential_backoff(self):
        # 500 (no Retry-After logic) should use exponential backoff unchanged.
        client = VoogClient(host="example.com", api_token="t")
        err_500 = _make_http_error(500, "Internal Server Error")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_500, _make_success_cm()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_post_429_not_retried(self):
        # POST must NOT retry even on 429 — duplicate-resource risk.
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_http_error(429, "Too Many Requests")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [err_429]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    client.post("/products", {"product": {}})
        self.assertEqual(ctx.exception.code, 429)
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()


class TestTimeoutNotRetried(unittest.TestCase):
    """T6 — socket.timeout must propagate immediately; no retry, no sleep."""

    def test_socket_timeout_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = socket.timeout("timed out")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(socket.timeout):
                    client.get("/pages")
        # Only one attempt — no retry on timeout.
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_builtin_timeout_error_not_retried(self):
        # In Python 3.10+, TimeoutError is an alias for socket.timeout.
        # Ensure the bare built-in form is also caught and not retried.
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("timed out")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(TimeoutError):
                    client.get("/pages")
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_timeout_not_retried_on_put(self):
        # PUT is in _RETRYABLE_METHODS but timeouts must still not retry.
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = socket.timeout("timed out")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(socket.timeout):
                    client.put("/resource", {"x": 1})
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_non_timeout_oserror_still_retries(self):
        # A non-timeout OSError (connection refused, DNS failure, TCP reset)
        # must still retry as before.
        client = VoogClient(host="example.com", api_token="t")
        with patch("voog.client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                OSError("Connection refused"),
                _make_success_cm(),
            ]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 2)
