"""Tests for voog._payloads — shared API payload builders."""

from __future__ import annotations

import unittest

from voog._payloads import build_product_payload, build_redirect_payload


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
        payload = build_product_payload(
            {"name": "Cap", "translations": {"name": {"et": "Müts"}}}
        )
        self.assertEqual(
            payload["product"]["translations"],
            {"name": {"et": "Müts"}},
        )

    def test_does_not_mutate_input(self):
        body = {"name": "Cap"}
        original = dict(body)
        build_product_payload(body)
        self.assertEqual(body, original)


if __name__ == "__main__":
    unittest.main()
