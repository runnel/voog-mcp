"""Tests for voog.mcp.tools.raw — generic Admin/Ecommerce passthrough."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from voog.mcp.tools import raw as raw_tools


class TestGetTools(unittest.TestCase):
    def test_four_tools_registered(self):
        names = [t.name for t in raw_tools.get_tools()]
        self.assertEqual(
            sorted(names),
            [
                "voog_admin_api_call",
                "voog_admin_api_read",
                "voog_ecommerce_api_call",
                "voog_ecommerce_api_read",
            ],
        )

    def test_admin_call_annotations(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        ann = tools["voog_admin_api_call"].annotations
        # Generic passthrough — any method possible, so the annotations
        # must be conservative.
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestAdminApiCall(unittest.TestCase):
    def test_put_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.put.return_value = {"id": 42, "title": "X"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PUT",
                "path": "/forms/42",
                "body": {"title": "X"},
            },
            client,
        )
        client.put.assert_called_once_with(
            "/forms/42",
            {"title": "X"},
            base="https://example.com/admin/api",
            params=None,
        )

    def test_post_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.post.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "POST",
                "path": "/articles",
                "body": {"page_id": 1, "autosaved_title": "Draft"},
            },
            client,
        )
        client.post.assert_called_once_with(
            "/articles",
            {"page_id": 1, "autosaved_title": "Draft"},
            base="https://example.com/admin/api",
            params=None,
        )

    def test_delete_request(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.delete.return_value = None
        raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/redirect_rules/9"},
            client,
        )
        client.delete.assert_called_once_with(
            "/redirect_rules/9",
            base="https://example.com/admin/api",
            params=None,
        )

    def test_patch_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.patch.return_value = {"id": 5, "title": "X"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PATCH",
                "path": "/site",
                "body": {"title": "X"},
            },
            client,
        )
        client.patch.assert_called_once_with(
            "/site",
            {"title": "X"},
            base="https://example.com/admin/api",
            params=None,
        )

    def test_rejects_percent_encoded_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/%2e%2e/%2e%2e/etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)

    def test_rejects_unknown_method(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "TRACE", "path": "/site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("method", payload["error"].lower())

    def test_rejects_path_without_leading_slash(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("/", payload["error"])

    def test_rejects_absolute_url(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "https://evil.example.com/exfil"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute", payload["error"].lower())

    def test_rejects_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/../../../../etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)

    def test_rejects_double_encoded_path_traversal(self):
        # /%252e%252e/etc/passwd decodes once to /%2e%2e/etc/passwd —
        # no literal '..' in either form. If any intermediate proxy
        # decodes a second time before routing, the request becomes
        # /../etc/passwd. Loop unquote until stable to catch this.
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/%252e%252e/etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("..", payload["error"])

    def test_rejects_path_with_query_when_params_set(self):
        # path containing '?' AND params= would produce a malformed URL
        # like /x?a=1?b=2 in client._request. Reject at the tool boundary
        # with a clear error.
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "DELETE",
                "path": "/forms?a=1",
                "params": {"b": "2"},
            },
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        # Error should mention either 'params' or '?' to point the
        # caller at the conflict.
        msg = payload["error"].lower()
        self.assertTrue("params" in msg or "?" in msg or "query" in msg)
        # Client must NOT have been called.
        client.delete.assert_not_called()

    def test_non_ascii_path_passthrough(self):
        # Estonian sites have ä/õ/š in slugs — verify the URL builder
        # forwards non-ASCII paths to the client unchanged. The client
        # handles encoding via urllib.
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.delete.return_value = {"id": 1, "slug": "töö"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/pages/töö-leht"},
            client,
        )
        client.delete.assert_called_once_with(
            "/pages/töö-leht",
            base="https://example.com/admin/api",
            params=None,
        )

    def test_api_error_propagates(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.put.side_effect = urllib.error.HTTPError(
            "url", 422, "Unprocessable Entity", {}, None
        )
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "PUT", "path": "/site", "body": {"title": "x"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("422", payload["error"])

    def test_post_forwards_params(self):
        # PR #124 review: POST/PUT/PATCH branches dropped `params` pre-fix
        # (only GET/DELETE forwarded it). S8 makes query-string filters
        # first-class; passthrough must forward params on every method.
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.post.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "POST",
                "path": "/articles",
                "body": {"x": 1},
                "params": {"include": "translations"},
            },
            client,
        )
        client.post.assert_called_once_with(
            "/articles",
            {"x": 1},
            base="https://example.com/admin/api",
            params={"include": "translations"},
        )

    def test_put_forwards_params(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.put.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PUT",
                "path": "/articles/7",
                "body": {"x": 1},
                "params": {"include": "translations"},
            },
            client,
        )
        client.put.assert_called_once_with(
            "/articles/7",
            {"x": 1},
            base="https://example.com/admin/api",
            params={"include": "translations"},
        )

    def test_patch_forwards_params(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.patch.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PATCH",
                "path": "/site",
                "body": {"x": 1},
                "params": {"include": "translations"},
            },
            client,
        )
        client.patch.assert_called_once_with(
            "/site",
            {"x": 1},
            base="https://example.com/admin/api",
            params={"include": "translations"},
        )


class TestEcommerceApiCall(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.delete.return_value = {}
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {"method": "DELETE", "path": "/products/7"},
            client,
        )
        client.delete.assert_called_once_with(
            "/products/7",
            base="https://example.com/admin/api/ecommerce/v1",
            params=None,
        )

    def test_put_settings(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"settings": {}}
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {
                "method": "PUT",
                "path": "/settings",
                "body": {"settings": {"translations": {"products_url_slug": {"en": "products"}}}},
            },
            client,
        )
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )


class TestAdminApiRead(unittest.TestCase):
    """voog_admin_api_read — readOnlyHint=true GET-only passthrough (S3)."""

    def test_in_get_tools(self):
        names = {t.name for t in raw_tools.get_tools()}
        self.assertIn("voog_admin_api_read", names)

    def test_annotations(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        ann = tools["voog_admin_api_read"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)

    def test_get_passthrough(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = [{"id": 1}]
        result = raw_tools.call_tool(
            "voog_admin_api_read",
            {"path": "/forms"},
            client,
        )
        client.get.assert_called_once_with(
            "/forms",
            base="https://example.com/admin/api",
            params=None,
        )
        body = json.loads(result[1].text)
        self.assertEqual(body, [{"id": 1}])

    def test_get_with_params(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = {"ok": True}
        raw_tools.call_tool(
            "voog_admin_api_read",
            {"path": "/articles", "params": {"q.article.title.$cont": "kuju"}},
            client,
        )
        client.get.assert_called_once_with(
            "/articles",
            base="https://example.com/admin/api",
            params={"q.article.title.$cont": "kuju"},
        )

    def test_rejects_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_read",
            {"path": "/../../etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)
        client.get.assert_not_called()

    def test_response_does_not_carry_deprecation_prefix(self):
        # The NEW read tool is non-deprecated — response body must not
        # start with "DEPRECATED:".
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = []
        result = raw_tools.call_tool(
            "voog_admin_api_read",
            {"path": "/forms"},
            client,
        )
        self.assertFalse(result[0].text.startswith("DEPRECATED:"))


class TestEcommerceApiRead(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in raw_tools.get_tools()}
        self.assertIn("voog_ecommerce_api_read", names)

    def test_uses_ecommerce_base(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = []
        raw_tools.call_tool(
            "voog_ecommerce_api_read",
            {"path": "/orders"},
            client,
        )
        client.get.assert_called_once_with(
            "/orders",
            base="https://example.com/admin/api/ecommerce/v1",
            params=None,
        )

    def test_annotations(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        ann = tools["voog_ecommerce_api_read"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestGetRemovedFromCallTools(unittest.TestCase):
    """v1.5 honours the v1.4 deprecation: the ``*_call`` tools no longer
    accept GET. v1.4 announced this on two channels (DeprecationWarning +
    a ``DEPRECATED:`` response prefix); both are gone with the branch."""

    def test_admin_schema_enum_omits_get(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        enum = tools["voog_admin_api_call"].inputSchema["properties"]["method"]["enum"]
        self.assertNotIn("GET", enum)
        self.assertEqual(sorted(enum), ["DELETE", "PATCH", "POST", "PUT"])

    def test_ecommerce_schema_enum_omits_get(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        enum = tools["voog_ecommerce_api_call"].inputSchema["properties"]["method"]["enum"]
        self.assertNotIn("GET", enum)

    def test_admin_get_is_refused_and_names_the_read_tool(self):
        # A host that doesn't enforce the schema enum must not get a
        # confusing 405 from Voog — it gets the migration target.
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/forms"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("voog_admin_api_read", payload["error"])
        self.assertIn("v1.5", payload["error"])
        client.get.assert_not_called()

    def test_ecommerce_get_is_refused_and_names_the_read_tool(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {"method": "GET", "path": "/orders"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("voog_ecommerce_api_read", payload["error"])
        client.get.assert_not_called()

    def test_get_no_longer_emits_a_deprecation_warning(self):
        # The warning channel existed to announce this removal; leaving it
        # firing after the removal would be noise on an error path.
        import warnings

        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            raw_tools.call_tool(
                "voog_admin_api_call",
                {"method": "GET", "path": "/forms"},
                client,
            )
        self.assertEqual([w for w in caught if issubclass(w.category, DeprecationWarning)], [])

    def test_write_methods_are_untouched(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.post.return_value = {"id": 7}
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "POST", "path": "/articles", "body": {"x": 1}},
            client,
        )
        self.assertFalse(result[0].text.startswith("DEPRECATED:"))
        client.post.assert_called_once()

    def test_read_tools_still_serve_get(self):
        # The capability moved rather than disappearing — the whole point.
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = [{"id": 1}]
        result = raw_tools.call_tool("voog_admin_api_read", {"path": "/forms"}, client)
        self.assertEqual(json.loads(result[1].text), [{"id": 1}])


class TestEcommercePassthroughGotchas(unittest.TestCase):
    """E10 (v1.4 phase 7): voog_ecommerce_api_call (write variant) must
    surface the three Voog ecommerce v1 PUT quirks in its description so
    the LLM doesn't fall into them via passthrough. Sentinels target the
    distinguishing words in each bullet — not the full sentence — so
    cosmetic edits (line wrapping, punctuation) don't break the test."""

    def _description(self) -> str:
        for t in raw_tools.get_tools():
            if t.name == "voog_ecommerce_api_call":
                return t.description
        self.fail("voog_ecommerce_api_call not in raw.get_tools()")
        return ""  # unreachable

    def test_assets_vs_asset_ids_gotcha(self):
        d = self._description()
        self.assertIn("asset_ids", d)
        self.assertIn("assets", d)
        self.assertIn("hero", d)
        self.assertIn("product_set_images", d)

    def test_variants_destructive_gotcha(self):
        d = self._description()
        self.assertIn("variants", d)
        self.assertIn("variant_attributes", d)
        self.assertIn("destructive", d)
        self.assertIn("product_update", d)

    def test_data_clobber_gotcha(self):
        d = self._description()
        self.assertIn("data", d)
        self.assertIn("PATCH", d)
        self.assertIn("merge semantics", d)
        # Per-key tool name appears at least once
        self.assertTrue("page_set_data" in d or "article_set_data" in d or "site_set_data" in d)
