"""Unit tests for VoogClient."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.client import VoogClient


class TestVoogClient(unittest.TestCase):
    def test_init_sets_base_urls(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.base_url, "https://runnel.ee/admin/api")
        self.assertEqual(client.ecommerce_url, "https://runnel.ee/admin/api/ecommerce/v1")

    def test_init_sets_headers(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.headers["X-API-Token"], "testtoken")
        self.assertEqual(client.headers["Content-Type"], "application/json")


class TestGetAllParamsPassthrough(unittest.TestCase):
    """get_all merges caller params with pagination params."""

    def _make_client(self):
        client = VoogClient(host="runnel.ee", api_token="t")
        # Mock the underlying single-page get to avoid real HTTP
        client.get = MagicMock(return_value=[])  # one empty page → loop exits
        return client

    def test_no_params_uses_only_pagination(self):
        client = self._make_client()
        client.get_all("/pages")
        client.get.assert_called_once_with(
            "/pages", base=None, params={"per_page": 100, "page": 1}
        )

    def test_caller_params_merged_with_pagination(self):
        client = self._make_client()
        client.get_all("/products", params={"include": "translations"})
        client.get.assert_called_once_with(
            "/products",
            base=None,
            params={"per_page": 100, "page": 1, "include": "translations"},
        )

    def test_base_kwarg_passed_through(self):
        client = self._make_client()
        client.get_all("/products", base=client.ecommerce_url, params={"include": "x"})
        args, kwargs = client.get.call_args
        self.assertEqual(kwargs["base"], client.ecommerce_url)

    def test_caller_params_can_override_pagination(self):
        # Documented behavior: caller params win — useful only if a future
        # endpoint needs a different per_page; not the common path.
        client = self._make_client()
        client.get_all("/x", params={"per_page": 50})
        args, kwargs = client.get.call_args
        self.assertEqual(kwargs["params"]["per_page"], 50)

    def test_pagination_increments_page_across_calls(self):
        client = VoogClient(host="runnel.ee", api_token="t")
        # First page returns full 100, second returns partial → loop exits
        client.get = MagicMock(side_effect=[
            [{"id": i} for i in range(100)],
            [{"id": 100}],
        ])
        result = client.get_all("/x", params={"include": "y"})
        self.assertEqual(len(result), 101)
        # Page 1 and 2 both got the include param
        first_call = client.get.call_args_list[0]
        second_call = client.get.call_args_list[1]
        self.assertEqual(first_call.kwargs["params"]["page"], 1)
        self.assertEqual(first_call.kwargs["params"]["include"], "y")
        self.assertEqual(second_call.kwargs["params"]["page"], 2)
        self.assertEqual(second_call.kwargs["params"]["include"], "y")
