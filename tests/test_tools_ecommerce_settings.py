"""Tests for voog.mcp.tools.ecommerce_settings."""

import unittest
from unittest.mock import MagicMock, patch

from voog.mcp.tools import ecommerce_settings as es


def _discovery_response(*keys: str) -> dict:
    """Build a {"translations": {key: {"en": "..."}}} discovery response
    for the requested keys (default to the v1.3 hardcoded set)."""
    if not keys:
        keys = ("products_url_slug", "terms_url", "company_name", "bank_details")
    return {"translations": {k: {"en": "..."} for k in keys}}


class TestGetTools(unittest.TestCase):
    def test_two_tools(self):
        names = sorted(t.name for t in es.get_tools())
        self.assertEqual(
            names,
            ["ecommerce_settings_get", "ecommerce_settings_update"],
        )


class TestGet(unittest.TestCase):
    def test_get_with_translations_include(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = {"settings": {}}
        es.call_tool("ecommerce_settings_get", {}, client)
        client.get.assert_called_once_with(
            "/settings",
            base="https://example.com/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )


class TestUpdate(unittest.TestCase):
    def setUp(self):
        # E11: clear the module-level discovery cache between tests so each
        # test starts with a known cold cache.
        es._TRANSLATABLE_KEYS_CACHE.clear()

    def _client(self, host: str = "example.com") -> MagicMock:
        client = MagicMock()
        client.host = host
        client.ecommerce_url = f"https://{host}/admin/api/ecommerce/v1"
        return client

    def test_update_currency_attr(self):
        client = self._client()
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {"attributes": {"currency": "EUR"}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(body["settings"]["currency"], "EUR")
        # No translations → no discovery GET.
        client.get.assert_not_called()

    def test_update_products_url_slug_translations(self):
        client = self._client()
        client.get.return_value = _discovery_response("products_url_slug")
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {
                "translations": {
                    "products_url_slug": {"en": "products"},
                }
            },
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )

    def test_rejects_empty_call(self):
        client = self._client()
        result = es.call_tool("ecommerce_settings_update", {}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_ecommerce_settings_update_rejects_string_translation_value(self):
        # A common LLM mistake: passing `translations={"products_url_slug":
        # "products"}` (string) instead of `{"products_url_slug": {"en":
        # "products"}}` (dict). Catch this client-side rather than letting
        # Voog return a generic 422.
        client = self._client()
        client.get.return_value = _discovery_response("products_url_slug")
        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": "products"}},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_ecommerce_settings_update_rejects_empty_translation_dict(self):
        # `{"products_url_slug": {}}` — dict shape but empty payload.
        client = self._client()
        client.get.return_value = _discovery_response("products_url_slug")
        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {}}},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_ecommerce_settings_update_rejects_empty_translation_lang_value(self):
        # `{"products_url_slug": {"et": ""}}` — Voog rejects empty
        # translation values, surface this client-side.
        client = self._client()
        client.get.return_value = _discovery_response("products_url_slug")
        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"et": ""}}},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestTranslatableSettingsRuntimeDiscovery(unittest.TestCase):
    """E11 (v1.4 phase 7): ecommerce_settings_update discovers the
    translatable-keys allowlist at runtime from
    GET /settings?include=translations rather than the v1.3 hardcoded
    frozenset. Cache key is site-name-only (verified in plan Task 1 R7 —
    translations top-level keys are language-agnostic settings keys,
    not language codes).

    Cache TTL is 60s; new keys server-side are picked up within 60s."""

    def setUp(self):
        es._TRANSLATABLE_KEYS_CACHE.clear()

    def _client(self, host: str = "stella.example.com") -> MagicMock:
        client = MagicMock()
        client.host = host
        client.ecommerce_url = f"https://{host}/admin/api/ecommerce/v1"
        return client

    def test_discovery_call_made_on_first_update(self):
        client = self._client()
        client.get.return_value = {
            "translations": {
                "products_url_slug": {"et": "tooted", "en": "products"},
                "terms_url": {"et": "tingimused", "en": "terms"},
                "company_name": {"et": "Stella", "en": "Stella"},
                "bank_details": {"et": "...", "en": "..."},
            }
        }
        client.put.return_value = {}

        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            client,
        )

        client.get.assert_called_once_with(
            "/settings",
            base=client.ecommerce_url,
            params={"include": "translations"},
        )
        self.assertEqual(client.put.call_count, 1)
        self.assertFalse(getattr(result, "isError", False))

    def test_discovery_skipped_on_second_call_within_ttl(self):
        client = self._client()
        client.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        client.put.return_value = {}

        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            client,
        )
        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "shop"}}},
            client,
        )

        self.assertEqual(client.get.call_count, 1, "discovery GET should be cached")
        self.assertEqual(client.put.call_count, 2)

    def test_discovery_re_runs_after_ttl_expiry(self):
        client = self._client()
        client.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        client.put.return_value = {}

        with patch("voog.mcp.tools.ecommerce_settings.time.monotonic") as mock_time:
            mock_time.return_value = 1000.0
            es.call_tool(
                "ecommerce_settings_update",
                {"translations": {"products_url_slug": {"en": "products"}}},
                client,
            )
            # Within TTL.
            mock_time.return_value = 1030.0
            es.call_tool(
                "ecommerce_settings_update",
                {"translations": {"products_url_slug": {"en": "shop"}}},
                client,
            )
            # Past TTL (60s default).
            mock_time.return_value = 1070.0
            es.call_tool(
                "ecommerce_settings_update",
                {"translations": {"products_url_slug": {"en": "store"}}},
                client,
            )

        # 2 discovery GETs: first call + post-expiry. Middle call hit cache.
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.put.call_count, 3)

    def test_cache_keyed_per_site(self):
        stella = self._client(host="stella.example.com")
        stella.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        stella.put.return_value = {}

        runnel = self._client(host="runnel.example.com")
        runnel.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        runnel.put.return_value = {}

        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            stella,
        )
        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            runnel,
        )

        # Each site's discovery GET happened once — caches are per-host.
        self.assertEqual(stella.get.call_count, 1)
        self.assertEqual(runnel.get.call_count, 1)

    def test_unknown_field_after_discovery_rejected(self):
        client = self._client()
        # Discovery returns a known minimal set.
        client.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        client.put.return_value = {}

        result = es.call_tool(
            "ecommerce_settings_update",
            # Server says products_url_slug is the only translatable key
            # right now — terms_url should be rejected with a clear error.
            {"translations": {"terms_url": {"en": "terms"}}},
            client,
        )

        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_new_server_side_key_accepted_automatically(self):
        """The whole point of E11: a key Voog adds server-side is picked
        up without a wrapper redeploy. Hardcoded v1.3 set wouldn't see it."""
        client = self._client()
        client.get.return_value = {
            "translations": {
                "products_url_slug": {"en": "products"},
                "terms_url": {"en": "terms"},
                "company_name": {"en": "Stella"},
                "bank_details": {"en": "..."},
                # Hypothetical new key Voog added server-side.
                "shipping_disclaimer": {"en": "Free shipping over €100"},
            }
        }
        client.put.return_value = {}

        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"shipping_disclaimer": {"en": "Free!"}}},
            client,
        )

        self.assertFalse(getattr(result, "isError", False))
        client.put.assert_called_once()

    def test_discovery_failure_surfaces_error(self):
        """If the discovery GET fails (Voog 5xx, network blip), we cannot
        validate the allowlist. Fail the call rather than guessing — the
        operator can retry, the cache stays cold, and no silent PUT goes
        out with possibly-invalid translation keys."""
        client = self._client()
        client.get.side_effect = RuntimeError("503 service unavailable")

        result = es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            client,
        )

        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_concurrent_calls_both_fetch_acceptable(self):
        """Race condition: two concurrent calls in the same site both miss
        cache, both fetch. Acceptable — discovery is a read-only GET, both
        results are identical, second write to cache is a no-op. Documented
        as acceptable in the plan; no lock needed."""
        client = self._client()
        client.get.return_value = {"translations": {"products_url_slug": {"en": "products"}}}
        client.put.return_value = {}

        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "products"}}},
            client,
        )
        # Force cache miss for the second call to simulate the race.
        es._TRANSLATABLE_KEYS_CACHE.clear()
        es.call_tool(
            "ecommerce_settings_update",
            {"translations": {"products_url_slug": {"en": "shop"}}},
            client,
        )

        # Both PUTs went through; both fetched. That's fine.
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.put.call_count, 2)
