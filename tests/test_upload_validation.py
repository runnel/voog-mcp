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
    def test_default_is_amazonaws_only(self):
        # PR #62 hardening: speculative .voog.com / .voogcdn.com entries
        # were dropped in favor of the narrowest possible default. Guard
        # against accidental re-broadening.
        self.assertEqual(_DEFAULT_UPLOAD_HOST_SUFFIXES, ("amazonaws.com",))

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


if __name__ == "__main__":
    unittest.main()
