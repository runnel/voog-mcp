"""Unit tests for voog._upload_validation.

Validator-only — exercises ``_validate_upload_url`` and
``_allowed_upload_host_suffixes`` in isolation. Caller-flow (orphan-error
surface, validator-not-called-when-blocked, etc.) is covered by the MCP and
CLI integration tests so this module stays drift-proof.
"""

import os
import unittest
from unittest.mock import patch

from voog._upload_validation import (
    _DEFAULT_UPLOAD_HOST_SUFFIXES,
    _allowed_upload_host_suffixes,
    _validate_upload_url,
)


class TestDefaults(unittest.TestCase):
    def test_default_is_amazonaws_plus_media_voog(self):
        # PR #62 hardening dropped speculative entries in favor of the
        # narrowest possible default; issue #137 added back the ONE host
        # Voog actually hands out (probed live: media.voog.com fronting a
        # presigned S3 URL). Guard against accidental re-broadening —
        # notably to a blanket "voog.com", which would cover the admin API.
        self.assertEqual(_DEFAULT_UPLOAD_HOST_SUFFIXES, ("amazonaws.com", "media.voog.com"))
        self.assertNotIn("voog.com", _DEFAULT_UPLOAD_HOST_SUFFIXES)

    def test_empty_env_uses_default(self):
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": ""}, clear=False):
            self.assertEqual(_allowed_upload_host_suffixes(), _DEFAULT_UPLOAD_HOST_SUFFIXES)

    def test_whitespace_only_env_falls_back_to_default(self):
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": "  ,  ,"}, clear=False):
            self.assertEqual(_allowed_upload_host_suffixes(), _DEFAULT_UPLOAD_HOST_SUFFIXES)

    def test_env_override_replaces_defaults(self):
        with patch.dict(
            os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": "voogcdn.com,example.org"}, clear=False
        ):
            self.assertEqual(_allowed_upload_host_suffixes(), ("voogcdn.com", "example.org"))

    def test_env_leading_dot_stripped(self):
        # Forgiving format: ".amazonaws.com" and "amazonaws.com" both work.
        with patch.dict(
            os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": ".voogcdn.com,.example.org"}, clear=False
        ):
            self.assertEqual(_allowed_upload_host_suffixes(), ("voogcdn.com", "example.org"))


class TestSchemeValidation(unittest.TestCase):
    def test_http_scheme_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url("http://voog-test.s3.amazonaws.com/up")
        self.assertIn("scheme", str(ctx.exception))
        self.assertIn("https", str(ctx.exception))

    def test_ftp_scheme_rejected(self):
        with self.assertRaises(ValueError):
            _validate_upload_url("ftp://voog-test.s3.amazonaws.com/up")

    def test_file_scheme_rejected(self):
        # file:// would let an attacker exfiltrate to local fs in some setups.
        with self.assertRaises(ValueError):
            _validate_upload_url("file:///etc/passwd")

    def test_https_scheme_accepted_when_host_in_allowlist(self):
        # No exception = accepted.
        _validate_upload_url("https://voog-test.s3.amazonaws.com/up?sig=abc")


class TestHostAllowlist(unittest.TestCase):
    def test_default_allows_amazonaws_subdomain(self):
        _validate_upload_url("https://voog-prod.s3.eu-west-1.amazonaws.com/u/201?sig=abc")

    def test_default_rejects_unknown_host(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url("https://attacker.example/upload")
        self.assertIn("attacker.example", str(ctx.exception))
        self.assertIn("allowlist", str(ctx.exception))

    def test_default_rejects_aws_metadata_ipv4(self):
        # Classic SSRF target — IPv4 literal must not match an FQDN allowlist.
        with self.assertRaises(ValueError):
            _validate_upload_url("https://169.254.169.254/latest/meta-data/")

    def test_default_rejects_loopback_ipv4(self):
        with self.assertRaises(ValueError):
            _validate_upload_url("https://127.0.0.1/upload")

    def test_default_rejects_rfc1918_ipv4(self):
        with self.assertRaises(ValueError):
            _validate_upload_url("https://192.168.1.1/upload")

    def test_query_string_does_not_break_parsing(self):
        # Presigned S3 URLs carry a long ?X-Amz-Signature=... payload.
        _validate_upload_url(
            "https://voog-prod.s3.eu-west-1.amazonaws.com/u/201"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef"
        )

    def test_uppercase_host_normalized(self):
        # urlparse already lowercases hostnames — guard the assumption.
        _validate_upload_url("https://VOOG-TEST.S3.AMAZONAWS.COM/up")

    def test_default_allows_voog_media_host(self):
        # Issue #137: the real shape of Voog's POST /assets response.
        # Probed live 2026-08-12 on the kolm-koma-2026 test site — the S3
        # presigned query string is intact, only the host is CNAME'd.
        _validate_upload_url(
            "https://media.voog.com/0000/0053/4382/photos/probe.gif"
            "?AWSAccessKeyId=AKIAIYLL3252EKOUNMDA&Expires=1786544615&Signature=lv1f%2BvO4%3D"
        )

    def test_default_rejects_voog_admin_host(self):
        # The reason the allowlist entry is media.voog.com and not a blanket
        # voog.com: a misbehaving API must not be able to steer the raw-bytes
        # PUT at an admin endpoint. Both the marketing host and a tenant host
        # (where confirm_url lives) must still fail.
        for url in (
            "https://www.voog.com/admin/api/assets/1/confirm",
            "https://voog.com/admin/api/assets/1/confirm",
            "https://kolm-koma-2026.voog.com/admin/api/assets/1/confirm",
        ):
            with self.assertRaises(ValueError) as ctx:
                _validate_upload_url(url)
            self.assertIn("allowlist", str(ctx.exception))

    def test_default_rejects_media_voog_lookalike_suffix(self):
        # Dot-boundary matching must not let an attacker-controlled parent
        # domain through by ending in the allowlisted string.
        with self.assertRaises(ValueError):
            _validate_upload_url("https://media.voog.com.attacker.example/upload")

    def test_default_allows_media_voog_subdomain(self):
        # Dot-boundary suffix semantics are shared with amazonaws.com; a
        # future shard host under the media store stays covered.
        _validate_upload_url("https://eu.media.voog.com/0000/0053/4382/photos/x.jpg?sig=abc")


class TestEnvOverride(unittest.TestCase):
    def test_env_override_accepts_only_configured_hosts(self):
        # Env replaces defaults entirely — amazonaws.com must NOT be allowed
        # if the override doesn't include it.
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": "voogcdn.com"}, clear=False):
            with self.assertRaises(ValueError):
                _validate_upload_url("https://voog-test.s3.amazonaws.com/up")
            _validate_upload_url("https://files.voogcdn.com/u/201")

    def test_env_no_substring_overmatch(self):
        # PR #62 regression: "evil.com" must not let "notevil.com" through.
        # The dot-boundary suffix match is the only suffix branch.
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": "evil.com"}, clear=False):
            with self.assertRaises(ValueError):
                _validate_upload_url("https://notevil.com/upload")

    def test_bare_host_match_accepted(self):
        # Allowlist entry "amazonaws.com" should match exactly "amazonaws.com"
        # (no subdomain) as well as "*.amazonaws.com".
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": "example.org"}, clear=False):
            _validate_upload_url("https://example.org/upload")

    def test_env_with_leading_dot_works_same(self):
        with patch.dict(os.environ, {"VOOG_UPLOAD_HOST_SUFFIXES": ".voogcdn.com"}, clear=False):
            _validate_upload_url("https://files.voogcdn.com/u/201")


class TestUserinfoRejection(unittest.TestCase):
    """S-4 hardening: URLs with userinfo prefixes are rejected.

    Legitimate presigned-S3 URLs never carry credentials in the URL
    (they use ``?X-Amz-Signature=`` query params); a userinfo prefix is
    a sign that an attacker is steering the audit log.
    """

    def test_userinfo_rejected_even_when_host_allowed(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url("https://attacker@voog-test.s3.amazonaws.com/up?sig=abc")
        self.assertIn("userinfo", str(ctx.exception))

    def test_userinfo_with_password_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url("https://user:pw@voog-test.s3.amazonaws.com/up")
        self.assertIn("userinfo", str(ctx.exception))

    def test_legitimate_no_userinfo_accepted(self):
        # Regression: the suffix matcher still accepts a clean URL.
        _validate_upload_url("https://voog-test.s3.amazonaws.com/up?sig=abc")


class TestIDNHomographDefense(unittest.TestCase):
    """S-4 hardening: cyrillic / look-alike characters in hostnames are
    rejected after IDN-to-ASCII normalisation. Digit-substitution
    variants like ``amaz0n.com`` were already rejected by the existing
    suffix matcher (zero is not in the allowlist); kept as a regression
    guard alongside the new cyrillic case.
    """

    def test_cyrillic_homograph_rejected(self):
        # First character is U+0430 CYRILLIC SMALL LETTER A, not U+0061.
        # Punycode form is xn--mazonaws-7l4d.com — fails the suffix match.
        host = "аmazonaws.com"
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url(f"https://{host}/upload")
        # Error message references the Punycode form (after normalisation)
        # or the IDN error itself — accept either branch.
        msg = str(ctx.exception)
        self.assertTrue(
            "xn--" in msg or "IDN" in msg or "allowlist" in msg,
            f"expected IDN/punycode/allowlist marker in {msg!r}",
        )

    def test_cyrillic_subdomain_homograph_rejected(self):
        # Subdomain-level homograph: "bucket.аmazonaws.com" (cyrillic in
        # second label).
        host = "bucket.аmazonaws.com"
        with self.assertRaises(ValueError):
            _validate_upload_url(f"https://{host}/upload")

    def test_digit_substitution_rejected(self):
        # `amaz0n.com` — zero substitution. Already covered by the
        # default suffix matcher (zero is not in "amazonaws.com"), but
        # kept here as a regression guard so the S-4 test class is the
        # single source of truth for hostname-deception cases.
        with self.assertRaises(ValueError) as ctx:
            _validate_upload_url("https://amaz0n.com/upload")
        self.assertIn("allowlist", str(ctx.exception))

    def test_legitimate_ascii_host_accepted(self):
        # Regression guard for the happy path after the IDN encoding
        # branch — pure-ASCII hosts must continue to pass.
        _validate_upload_url("https://voog-prod.s3.amazonaws.com/u/201?sig=abc")


if __name__ == "__main__":
    unittest.main()
