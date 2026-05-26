"""Unit tests for VoogClient."""

import unittest
from unittest.mock import MagicMock, patch

import httpx

import voog
from voog.client import VoogClient, _parse_retry_after


def _make_httpx_response(
    status_code: int = 200,
    body: bytes = b'{"ok": true}',
    headers: dict | None = None,
    url: str = "https://example.com/admin/api/pages",
) -> httpx.Response:
    """Build an httpx.Response with a pre-attached Request (raise_for_status needs it)."""
    req = httpx.Request("GET", url)
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers=headers or {},
        request=req,
    )


def _make_status_error(
    status_code: int,
    msg: str,
    retry_after: str | None = None,
    method: str = "GET",
    url: str = "https://example.com/admin/api/pages",
) -> httpx.HTTPStatusError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    req = httpx.Request(method, url)
    resp = httpx.Response(status_code=status_code, request=req, headers=headers, content=b"")
    return httpx.HTTPStatusError(message=msg, request=req, response=resp)


class TestVoogClient(unittest.TestCase):
    def test_init_sets_base_urls(self):
        client = VoogClient(host="example.com", api_token="testtoken")
        self.assertEqual(client.base_url, "https://example.com/admin/api")
        self.assertEqual(client.ecommerce_url, "https://example.com/admin/api/ecommerce/v1")

    def test_init_sets_headers(self):
        client = VoogClient(host="example.com", api_token="testtoken")
        self.assertEqual(client.headers["X-API-Token"], "testtoken")
        self.assertEqual(client.headers["Content-Type"], "application/json")


class TestUserAgent(unittest.TestCase):
    """N1 — User-Agent must derive from voog.__version__, not a hardcoded literal."""

    def test_user_agent_uses_version_constant(self):
        client = VoogClient(host="example.com", api_token="t")
        self.assertEqual(client.headers["User-Agent"], f"voog-mcp/{voog.__version__}")

    def test_user_agent_changes_when_version_changes(self):
        # Patch the imported __version__ — confirms client.py reads from
        # the constant at construction time rather than baking in a literal.
        with patch.object(voog, "__version__", "9.9.9-test"):
            client = VoogClient(host="example.com", api_token="t")
            self.assertEqual(client.headers["User-Agent"], "voog-mcp/9.9.9-test")


class TestHttpxClientLifecycle(unittest.TestCase):
    """S11 — VoogClient owns a single httpx.Client (pool reuse, HTTP/2 keepalive)."""

    def test_httpx_client_is_singleton_per_instance(self):
        client = VoogClient(host="example.com", api_token="t")
        # Two requests must reuse the same httpx.Client instance.
        # (Pool reuse is the whole point of S11.)
        self.assertIs(client._http_client, client._http_client)

    def test_http_client_follows_redirects(self):
        """urllib parity: urlopen followed redirects by default (max 10).

        httpx's default is ``follow_redirects=False`` — a 301/302 from
        Voog/Cloudflare (trailing slash, www↔apex, asset-host CDN) would
        otherwise escalate via ``raise_for_status`` as a 3xx
        ``HTTPStatusError`` (302 is not in ``_RETRYABLE_STATUS``, so the
        retry branch immediately re-raises). Pinning ``follow_redirects=True``
        preserves the pre-1.4 behaviour callers rely on.
        """
        client = VoogClient(host="example.com", api_token="t")
        self.assertTrue(client._http_client.follow_redirects)

    def test_too_many_redirects_raises_immediately_no_retry(self):
        """TooManyRedirects is permanent — retrying wastes 2 attempts.

        ``httpx.TooManyRedirects`` inherits from ``RequestError`` →
        ``HTTPError``, so without an explicit branch it would be caught
        by ``except httpx.HTTPError`` in ``_request`` and retried up to
        ``max_retries`` times. The dedicated ``except TooManyRedirects``
        branch in ``_request`` re-raises immediately to short-circuit.
        """
        client = VoogClient(host="example.com", api_token="t")
        req = httpx.Request("GET", "https://example.com/pages")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = httpx.TooManyRedirects("too many", request=req)
            with self.assertRaises(httpx.TooManyRedirects):
                client.get("/pages")
        # Single attempt, no retries.
        self.assertEqual(mock_req.call_count, 1)


class TestVoogClientTimeout(unittest.TestCase):
    """HTTP timeout — long-running MCP server cannot afford to hang."""

    def test_default_timeout_is_60_seconds(self):
        client = VoogClient(host="example.com", api_token="t")
        self.assertEqual(client.timeout, 60)

    def test_custom_timeout_stored(self):
        client = VoogClient(host="example.com", api_token="t", timeout=15)
        self.assertEqual(client.timeout, 15)

    def test_request_passes_timeout_to_httpx(self):
        client = VoogClient(host="example.com", api_token="t", timeout=15)
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.get("/pages")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("timeout"), 15)

    def test_request_uses_default_timeout_when_not_overridden(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.get("/pages")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("timeout"), 60)


class TestRequestUrlEncoding(unittest.TestCase):
    """Querystring assembly — httpx handles encoding internally via params=."""

    def test_request_urlencodes_keys_with_special_chars(self):
        client = VoogClient(host="x.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.get("/x", params={"foo bar": "y"})
        # httpx forwards params to its own URL builder; assert the params
        # kwarg made it through verbatim (httpx encodes at send time).
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params"), {"foo bar": "y"})

    def test_request_urlencodes_values_with_special_chars(self):
        client = VoogClient(host="x.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.get("/x", params={"include": "variant_types,translations"})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params"), {"include": "variant_types,translations"})


class TestPatchMethod(unittest.TestCase):
    """VoogClient.patch() mirrors put/post/delete — and now also accepts params=."""

    def test_patch_sends_correct_method_and_body(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response(body=b'{"id": 5}')
            result = client.patch("/site", {"title": "X"})
        args, kwargs = mock_req.call_args
        # httpx.Client.request signature: (method, url, ...)
        self.assertEqual(kwargs.get("method") or args[0], "PATCH")
        self.assertEqual(result, {"id": 5})

    def test_patch_with_custom_base(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.patch("/settings", {"x": 1}, base="https://example.com/admin/api/ecommerce/v1")
        _, kwargs = mock_req.call_args
        url = kwargs.get("url") or mock_req.call_args.args[1]
        self.assertIn("ecommerce/v1/settings", str(url))

    def test_patch_forwards_params(self):
        # NEW — Phase 2 S4 will need this. Verifies the bonus signature
        # normalisation actually wires `params` through to the transport.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.patch("/site", {"x": 1}, params={"include": "foo"})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params"), {"include": "foo"})

    def test_patch_without_params_does_not_pass_params(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.patch("/site", {"x": 1})
        _, kwargs = mock_req.call_args
        # Either None or missing — both acceptable.
        self.assertIn(kwargs.get("params"), (None, {}))


class TestGetAllParamsPassthrough(unittest.TestCase):
    """get_all merges caller params with pagination params."""

    def _make_client(self):
        client = VoogClient(host="example.com", api_token="t")
        client.get = MagicMock(return_value=[])
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
        _, kwargs = client.get.call_args
        self.assertEqual(kwargs["base"], client.ecommerce_url)

    def test_caller_can_override_per_page(self):
        client = self._make_client()
        client.get_all("/x", params={"per_page": 50})
        _, kwargs = client.get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 50)

    def test_caller_page_param_ignored(self):
        client = VoogClient(host="example.com", api_token="t")
        client.get = MagicMock(
            side_effect=[
                [{"id": i} for i in range(200)],
                [{"id": 200}],
            ]
        )
        client.get_all("/x", params={"page": 99})
        first_call = client.get.call_args_list[0]
        second_call = client.get.call_args_list[1]
        self.assertEqual(first_call.kwargs["params"]["page"], 1)
        self.assertEqual(second_call.kwargs["params"]["page"], 2)

    def test_pagination_increments_page_across_calls(self):
        client = VoogClient(host="example.com", api_token="t")
        client.get = MagicMock(
            side_effect=[
                [{"id": i} for i in range(200)],
                [{"id": 200}],
            ]
        )
        result = client.get_all("/x", params={"include": "y"})
        self.assertEqual(len(result), 201)
        first_call = client.get.call_args_list[0]
        second_call = client.get.call_args_list[1]
        self.assertEqual(first_call.kwargs["params"]["page"], 1)
        self.assertEqual(first_call.kwargs["params"]["include"], "y")
        self.assertEqual(second_call.kwargs["params"]["page"], 2)
        self.assertEqual(second_call.kwargs["params"]["include"], "y")


class TestGetAllPagination(unittest.TestCase):
    """Regression guards for B3 (audit 03-bugs-and-correctness.md)."""

    def test_terminates_when_page_short_under_caller_per_page(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = [{"id": i} for i in range(150)]
            results = client.get_all("/pages", params={"per_page": 250})
        self.assertEqual(len(results), 150)
        mock_get.assert_called_once()

    def test_terminates_when_caller_per_page_full_page_then_empty(self):
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
        # NOTE: MD1 in PR 1b bumps this to 250 and renames the test.
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = []
            client.get_all("/pages")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 200)

    def test_caller_per_page_override_wins(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client, "get") as mock_get:
            mock_get.return_value = []
            client.get_all("/pages", params={"per_page": 250})
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 250)


class TestRequestRetry(unittest.TestCase):
    """Audit I9 — retry on transient failures (5xx, network errors)."""

    def test_5xx_is_retried_then_succeeds(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_req.call_count, 2)
        mock_sleep.assert_called_once_with(0.5)

    def test_5xx_response_object_triggers_retry_via_raise_for_status(self):
        """B3 — production path: httpx returns a 503 Response object, and
        the retry branch fires via resp.raise_for_status() (NOT because
        mock.request raised). The existing test_5xx_is_retried_then_succeeds
        uses side_effect to raise the HTTPStatusError BEFORE
        raise_for_status runs, which means the `resp.raise_for_status()`
        production line is never actually exercised. This test fixes that
        gap by handing back a real httpx.Response(status_code=503, ...)
        on the first call and a 200 Response on the second — exactly the
        wire-level behaviour of a real 503-then-success retry.
        """
        client = VoogClient(host="example.com", api_token="t")
        req = httpx.Request("GET", "https://example.com/admin/api/pages")
        resp_503 = httpx.Response(
            status_code=503,
            request=req,
            headers={},
            content=b"",
        )
        resp_200 = _make_httpx_response()
        with patch.object(client._http_client, "request") as mock_req:
            # NB: return_value list via side_effect of Response objects
            # (NOT exceptions). raise_for_status() inside _request must
            # be the thing that converts 503 → HTTPStatusError, then
            # the except-branch retries.
            mock_req.side_effect = [resp_503, resp_200]
            with patch("voog.client.time.sleep") as mock_sleep:
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_req.call_count, 2)
        mock_sleep.assert_called_once_with(0.5)

    def test_500_then_500_then_success(self):
        client = VoogClient(host="example.com", api_token="t")
        err_500 = _make_status_error(500, "Internal")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_500, err_500, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        self.assertEqual(mock_req.call_count, 3)
        self.assertEqual(
            [c.args[0] for c in mock_sleep.call_args_list],
            [0.5, 1.0],
        )

    def test_5xx_exhausted_raises(self):
        client = VoogClient(host="example.com", api_token="t")
        err_502 = _make_status_error(502, "Bad Gateway")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_502, err_502, err_502]
            with patch("voog.client.time.sleep"):
                with self.assertRaises(httpx.HTTPStatusError) as ctx:
                    client.get("/pages")
        self.assertEqual(ctx.exception.response.status_code, 502)
        self.assertEqual(mock_req.call_count, 3)

    def test_4xx_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_422 = _make_status_error(422, "Unprocessable")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_422]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    client.get("/pages")
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_network_error_is_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [
                httpx.ConnectError("connection reset by peer"),
                _make_httpx_response(),
            ]
            with patch("voog.client.time.sleep"):
                client.get("/pages")
        self.assertEqual(mock_req.call_count, 2)

    def test_network_error_exhausted_raises(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = httpx.ConnectError("permanent disconnect")
            with patch("voog.client.time.sleep"):
                with self.assertRaises(httpx.ConnectError):
                    client.get("/pages")
        self.assertEqual(mock_req.call_count, 3)

    def test_successful_first_try_no_retry(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()


class TestRequestRetryMethodGating(unittest.TestCase):
    """PR #110 review fix preserved through httpx swap — POST/PATCH must NOT retry."""

    def test_post_5xx_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable", method="POST")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(httpx.HTTPStatusError) as ctx:
                    client.post("/products", {"product": {}})
        self.assertEqual(ctx.exception.response.status_code, 503)
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_post_network_error_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [httpx.ConnectError("connection reset")]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(httpx.ConnectError):
                    client.post("/products", {"product": {}})
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_patch_5xx_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_502 = _make_status_error(502, "Bad Gateway", method="PATCH")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_502]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    client.patch("/resource", {})
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_put_5xx_still_retries(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable", method="PUT")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep"):
                client.put("/resource", {"key": "v"})
        self.assertEqual(mock_req.call_count, 2)

    def test_delete_5xx_still_retries(self):
        client = VoogClient(host="example.com", api_token="t")
        err_500 = _make_status_error(500, "Internal", method="DELETE")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_500, _make_httpx_response(body=b"")]
            with patch("voog.client.time.sleep"):
                client.delete("/pages/42")
        self.assertEqual(mock_req.call_count, 2)


class TestRequestLogging(unittest.TestCase):
    """Audit I17 — _request emits debug logs for traceability."""

    def test_request_debug_logs_method_and_url(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            with self.assertLogs("voog.client", level="DEBUG") as ctx:
                client.get("/pages")
        debug_msgs = [r.getMessage() for r in ctx.records if r.levelname == "DEBUG"]
        self.assertTrue(
            any("GET" in m and "/pages" in m for m in debug_msgs),
            f"Expected GET /pages in debug logs, got: {debug_msgs}",
        )

    def test_5xx_retry_emits_warning(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep"):
                with self.assertLogs("voog.client", level="WARNING") as ctx:
                    client.get("/pages")
        warning_msgs = [r.getMessage() for r in ctx.records if r.levelname == "WARNING"]
        self.assertTrue(
            any("503" in m for m in warning_msgs),
            f"Expected 503 retry warning, got: {warning_msgs}",
        )

    def test_network_error_retry_emits_warning(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [
                httpx.ConnectError("connection reset"),
                _make_httpx_response(),
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
    """PUT /nodes/{id}/move uses query-string params per Voog docs."""

    def _client(self):
        return VoogClient(host="example.com", api_token="t")

    def test_put_forwards_params(self):
        client = self._client()
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.put("/nodes/3/move", params={"parent_id": 2, "position": 1})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params"), {"parent_id": 2, "position": 1})

    def test_put_without_params_unchanged(self):
        client = self._client()
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.put("/articles/7", data={"autosaved_title": "x"})
        _, kwargs = mock_req.call_args
        self.assertIn(kwargs.get("params"), (None, {}))

    def test_post_forwards_params(self):
        client = self._client()
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.post("/foo", data={"x": 1}, params={"include": "bar"})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params"), {"include": "bar"})

    def test_post_without_params_unchanged(self):
        client = self._client()
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.return_value = _make_httpx_response()
            client.post("/products", data={"product": {"name": "x"}})
        _, kwargs = mock_req.call_args
        self.assertIn(kwargs.get("params"), (None, {}))


class TestParseRetryAfter(unittest.TestCase):
    """Unit tests for the _parse_retry_after helper (unchanged across transport swap)."""

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
        self.assertEqual(
            _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", fallback=1.0),
            1.0,
        )

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_retry_after("  15  ", fallback=0.5), 15.0)


class TestRateLimitRetry(unittest.TestCase):
    """T6 — 429 Too Many Requests retry + Retry-After header honoring."""

    def test_429_retried_then_succeeds(self):
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_status_error(429, "Too Many Requests")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429, _make_httpx_response()]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_req.call_count, 2)

    def test_429_exhausted_raises(self):
        client = VoogClient(host="example.com", api_token="t", max_retries=2)
        err_429 = _make_status_error(429, "Too Many Requests")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429, err_429, err_429]
            with patch("voog.client.time.sleep"):
                with self.assertRaises(httpx.HTTPStatusError) as ctx:
                    client.get("/pages")
        self.assertEqual(ctx.exception.response.status_code, 429)
        self.assertEqual(mock_req.call_count, 3)

    def test_503_still_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_req.call_count, 2)

    def test_retry_after_integer_honored_on_429(self):
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_status_error(429, "Too Many Requests", retry_after="5")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(5.0)

    def test_retry_after_honored_on_503(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable", retry_after="10")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(10.0)

    def test_retry_after_cap_at_60(self):
        client = VoogClient(host="example.com", api_token="t")
        err_503 = _make_status_error(503, "Service Unavailable", retry_after="600")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_503, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(60.0)

    def test_retry_after_invalid_uses_exponential_backoff(self):
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_status_error(429, "Too Many Requests", retry_after="not-a-number")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_no_retry_after_uses_exponential_backoff_for_429(self):
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_status_error(429, "Too Many Requests")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_5xx_no_retry_after_still_uses_exponential_backoff(self):
        client = VoogClient(host="example.com", api_token="t")
        err_500 = _make_status_error(500, "Internal Server Error")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_500, _make_httpx_response()]
            with patch("voog.client.time.sleep") as mock_sleep:
                client.get("/pages")
        mock_sleep.assert_called_once_with(0.5)

    def test_post_429_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        err_429 = _make_status_error(429, "Too Many Requests", method="POST")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [err_429]
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(httpx.HTTPStatusError) as ctx:
                    client.post("/products", {"product": {}})
        self.assertEqual(ctx.exception.response.status_code, 429)
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()


class TestTimeoutNotRetried(unittest.TestCase):
    """T6 — httpx.TimeoutException must propagate immediately; no retry, no sleep."""

    def test_httpx_timeout_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = httpx.ReadTimeout("timed out")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(TimeoutError):
                    client.get("/pages")
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_httpx_connect_timeout_not_retried(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = httpx.ConnectTimeout("connect timeout")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(TimeoutError):
                    client.get("/pages")
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_timeout_not_retried_on_put(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = httpx.ReadTimeout("timed out")
            with patch("voog.client.time.sleep") as mock_sleep:
                with self.assertRaises(TimeoutError):
                    client.put("/resource", {"x": 1})
        mock_req.assert_called_once()
        mock_sleep.assert_not_called()

    def test_non_timeout_network_error_still_retries(self):
        client = VoogClient(host="example.com", api_token="t")
        with patch.object(client._http_client, "request") as mock_req:
            mock_req.side_effect = [
                httpx.ConnectError("Connection refused"),
                _make_httpx_response(),
            ]
            with patch("voog.client.time.sleep"):
                result = client.get("/pages")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_req.call_count, 2)
