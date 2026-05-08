"""Tests for voog._payloads — shared API payload builders."""

from __future__ import annotations

import unittest

from voog._payloads import (
    build_article_payload,
    build_product_payload,
    build_redirect_envelope,
    build_redirect_payload,
    build_settings_payload,
)


class TestBuildRedirectPayload(unittest.TestCase):
    def test_default_redirect_type_is_301(self):
        payload = build_redirect_payload("/old", "/new")
        self.assertEqual(payload["redirect_rule"]["redirect_type"], 301)

    def test_active_defaults_true(self):
        payload = build_redirect_payload("/old", "/new")
        self.assertIs(payload["redirect_rule"]["active"], True)

    def test_destination_field_name_not_target(self):
        # Voog API expects ``destination``, not ``target``. If the field name
        # ever drifts back, this regression-guard catches it before shipping.
        payload = build_redirect_payload("/old", "/new")
        rule = payload["redirect_rule"]
        self.assertIn("destination", rule)
        self.assertNotIn("target", rule)
        self.assertEqual(rule["destination"], "/new")
        self.assertEqual(rule["source"], "/old")

    def test_explicit_redirect_type_and_active(self):
        payload = build_redirect_payload("/old", "/new", redirect_type=410, active=False)
        self.assertEqual(
            payload,
            {
                "redirect_rule": {
                    "source": "/old",
                    "destination": "/new",
                    "redirect_type": 410,
                    "active": False,
                    "regexp": False,
                }
            },
        )

    def test_regexp_defaults_false(self):
        payload = build_redirect_payload("/old", "/new")
        self.assertIs(payload["redirect_rule"]["regexp"], False)

    def test_regexp_can_be_set_true(self):
        payload = build_redirect_payload("/old/.*", "/new", regexp=True)
        self.assertIs(payload["redirect_rule"]["regexp"], True)

    def test_all_fields_present(self):
        # Full envelope shape — regression guard for any future schema drift.
        payload = build_redirect_payload(
            "/old", "/new", redirect_type=302, active=False, regexp=True
        )
        self.assertEqual(
            payload,
            {
                "redirect_rule": {
                    "source": "/old",
                    "destination": "/new",
                    "redirect_type": 302,
                    "active": False,
                    "regexp": True,
                }
            },
        )


class TestBuildProductPayload(unittest.TestCase):
    def test_wraps_attributes_in_envelope(self):
        payload = build_product_payload({"name": "Cap", "price": 21})
        self.assertEqual(
            payload,
            {"product": {"name": "Cap", "price": 21}},
        )

    def test_empty_body_still_wrapped(self):
        # The builder is a pure wrapper — caller is responsible for
        # validating non-empty content. Empty body is the caller's bug,
        # not the builder's.
        payload = build_product_payload({})
        self.assertEqual(payload, {"product": {}})

    def test_translations_passed_through(self):
        payload = build_product_payload({"name": "Cap", "translations": {"name": {"et": "Müts"}}})
        self.assertEqual(
            payload["product"]["translations"],
            {"name": {"et": "Müts"}},
        )

    def test_does_not_mutate_input(self):
        body = {"name": "Cap"}
        original = dict(body)
        build_product_payload(body)
        self.assertEqual(body, original)


class TestBuildSettingsPayload(unittest.TestCase):
    def test_wraps_in_settings_envelope(self):
        payload = build_settings_payload({"currency": "EUR"})
        self.assertEqual(payload, {"settings": {"currency": "EUR"}})

    def test_empty_body(self):
        payload = build_settings_payload({})
        self.assertEqual(payload, {"settings": {}})

    def test_translations_passed_through(self):
        body = {
            "currency": "EUR",
            "translations": {"products_url_slug": {"en": "products"}},
        }
        payload = build_settings_payload(body)
        self.assertEqual(payload["settings"]["translations"], body["translations"])

    def test_does_not_mutate_input(self):
        body = {"currency": "EUR"}
        original = dict(body)
        build_settings_payload(body)
        self.assertEqual(body, original)


class TestBuildRedirectEnvelope(unittest.TestCase):
    """PR #111 review nit: symmetric envelope builder for redirect_update's
    GET-merge-PUT path (mirrors build_product_payload / build_settings_payload).
    """

    def test_wraps_in_redirect_rule_envelope(self):
        payload = build_redirect_envelope({"source": "/old", "active": True})
        self.assertEqual(
            payload,
            {"redirect_rule": {"source": "/old", "active": True}},
        )

    def test_empty_body_still_wrapped(self):
        payload = build_redirect_envelope({})
        self.assertEqual(payload, {"redirect_rule": {}})

    def test_does_not_mutate_input(self):
        body = {"source": "/old", "regexp": False}
        original = dict(body)
        build_redirect_envelope(body)
        self.assertEqual(body, original)


class TestBuildArticlePayload(unittest.TestCase):
    """Article body shaping (audit I11 follow-up). Flat body, autosaved_* mapping."""

    def test_maps_title_to_autosaved_title(self):
        body = build_article_payload({"title": "Hello"})
        self.assertEqual(body, {"autosaved_title": "Hello"})

    def test_maps_body_to_autosaved_body(self):
        body = build_article_payload({"body": "<p>x</p>"})
        self.assertEqual(body, {"autosaved_body": "<p>x</p>"})

    def test_maps_excerpt_to_autosaved_excerpt(self):
        body = build_article_payload({"excerpt": "summary"})
        self.assertEqual(body, {"autosaved_excerpt": "summary"})

    def test_passes_through_description(self):
        body = build_article_payload({"description": "meta desc"})
        self.assertEqual(body, {"description": "meta desc"})

    def test_passes_through_path_image_id_tag_names_data(self):
        body = build_article_payload(
            {
                "path": "/blog/post",
                "image_id": 42,
                "tag_names": ["news", "tech"],
                "data": {"custom": "v"},
            }
        )
        self.assertEqual(
            body,
            {
                "path": "/blog/post",
                "image_id": 42,
                "tag_names": ["news", "tech"],
                "data": {"custom": "v"},
            },
        )

    def test_empty_arguments_yields_empty_body(self):
        body = build_article_payload({})
        self.assertEqual(body, {})

    def test_missing_keys_skipped(self):
        body = build_article_payload({"title": "X"})
        self.assertNotIn("autosaved_body", body)
        self.assertNotIn("description", body)

    def test_explicit_none_skipped(self):
        body = build_article_payload({"title": None, "body": ""})
        self.assertNotIn("autosaved_title", body)
        self.assertEqual(body["autosaved_body"], "")

    def test_publish_flag_off_by_default(self):
        body = build_article_payload({"title": "X", "publish": True})
        self.assertNotIn("publishing", body)

    def test_publish_flag_included_when_requested(self):
        body = build_article_payload({"title": "X", "publish": True}, include_publish=True)
        self.assertIs(body["publishing"], True)

    def test_publish_false_does_not_set_publishing(self):
        body = build_article_payload({"title": "X", "publish": False}, include_publish=True)
        self.assertNotIn("publishing", body)

    def test_does_not_mutate_input(self):
        args = {"title": "X", "body": "Y"}
        original = dict(args)
        build_article_payload(args)
        self.assertEqual(args, original)


if __name__ == "__main__":
    unittest.main()
