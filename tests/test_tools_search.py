"""Tests for voog.mcp.tools.search — full-text search wrapper."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import search as st


class TestGetTools(unittest.TestCase):
    def test_one_tool_registered(self):
        names = sorted(t.name for t in st.get_tools())
        self.assertEqual(names, ["voog_search"])


class TestVoogSearch(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in st.get_tools()}
        self.assertIn("voog_search", names)

    def test_happy_path_with_hits(self):
        client = MagicMock()
        client.get.return_value = [
            {"kind": "page", "id": 1, "title": "Hello", "path": "/hello"},
            {"kind": "article", "id": 5, "title": "Hello world", "path": "/blog/hello"},
            {"kind": "page", "id": 7, "title": "Second hello", "path": "/x"},
        ]
        result = st.call_tool("voog_search", {"q": "hello"}, client)
        # Exactly one /search call — no sentinel needed on non-zero hits.
        self.assertEqual(client.get.call_count, 1)
        client.get.assert_called_with("/search", params={"q": "hello"})
        body = json.loads(result[1].text)
        self.assertEqual(len(body["hits"]), 3)
        self.assertEqual(body["kind_counts"], {"page": 2, "article": 1})

    def test_scope_filter_forwarded(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return []
            raise AssertionError(f"unexpected path: {path}")

        client.get.side_effect = dispatch
        st.call_tool(
            "voog_search",
            {"q": "x", "scope": "articles", "language_code": "et", "per_page": 50},
            client,
        )
        first_call = client.get.call_args_list[0]
        self.assertEqual(first_call.args[0], "/search")
        params = first_call.kwargs["params"]
        self.assertEqual(params["q"], "x")
        self.assertEqual(params["scope"], "articles")
        self.assertEqual(params["language_code"], "et")
        self.assertEqual(params["per_page"], 50)

    def test_scope_all_omits_scope_param(self):
        client = MagicMock()
        client.get.return_value = [{"kind": "page", "id": 1, "title": "x", "path": "/x"}]
        st.call_tool("voog_search", {"q": "x", "scope": "all"}, client)
        params = client.get.call_args.kwargs["params"]
        self.assertNotIn("scope", params)

    def test_requires_q(self):
        client = MagicMock()
        result = st.call_tool("voog_search", {}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_rejects_whitespace_q(self):
        client = MagicMock()
        result = st.call_tool("voog_search", {"q": "   "}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_invalid_scope_rejected(self):
        client = MagicMock()
        result = st.call_tool("voog_search", {"q": "x", "scope": "tickets"}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_md5_indexing_off_detection(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return [{"id": 1, "title": "Sentinel Page Title"}]
            raise AssertionError(f"unexpected path: {path}")

        client.get.side_effect = dispatch
        result = st.call_tool("voog_search", {"q": "anything"}, client)
        body = json.loads(result[1].text)
        self.assertTrue(body.get("indexing_disabled"))
        self.assertIn("indexing", body.get("hint", "").lower())
        # Three calls total: original search + pages_list sentinel + sentinel search.
        self.assertEqual(client.get.call_count, 3)

    def test_md5_no_pages_means_honest_zero(self):
        client = MagicMock()

        def dispatch(path, params=None):
            if path == "/search":
                return []
            if path == "/pages":
                return []
            raise AssertionError(f"unexpected path: {path}")

        client.get.side_effect = dispatch
        result = st.call_tool("voog_search", {"q": "anything"}, client)
        body = json.loads(result[1].text)
        self.assertNotIn("indexing_disabled", body)
        self.assertEqual(body["hits"], [])

    def test_md5_sentinel_succeeds_means_honest_zero(self):
        client = MagicMock()
        call_count = {"n": 0}

        def dispatch(path, params=None):
            call_count["n"] += 1
            if path == "/search" and call_count["n"] == 1:
                return []
            if path == "/pages":
                return [{"id": 1, "title": "Real Page"}]
            if path == "/search":
                return [{"kind": "page", "id": 1, "title": "Real Page", "path": "/"}]
            raise AssertionError(f"unexpected: {path} (call {call_count['n']})")

        client.get.side_effect = dispatch
        result = st.call_tool("voog_search", {"q": "no-such-string"}, client)
        body = json.loads(result[1].text)
        self.assertNotIn("indexing_disabled", body)

    def test_unexpected_response_shape(self):
        client = MagicMock()
        client.get.return_value = {"not": "a list"}
        result = st.call_tool("voog_search", {"q": "x"}, client)
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in st.get_tools()}
        ann = tools["voog_search"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)

    def test_per_page_schema_enforces_voog_max(self):
        # MD1 silent-cap mitigation — schema declares the bound so
        # JSON-Schema-aware MCP hosts reject `per_page=1000` before
        # the round-trip, surfacing the cap to the LLM in a useful
        # way rather than letting Voog silently truncate at 250.
        tools = {t.name: t for t in st.get_tools()}
        prop = tools["voog_search"].inputSchema["properties"]["per_page"]
        self.assertEqual(prop["maximum"], 250)
        self.assertEqual(prop["minimum"], 1)


class TestServerToolRegistry(unittest.TestCase):
    def test_search_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(st, server.TOOL_GROUPS)
