"""Unit tests for VoogClient."""

import unittest
from unittest.mock import MagicMock, patch

from voog.client import VoogClient


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
