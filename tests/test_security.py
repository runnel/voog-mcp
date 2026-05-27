"""Unit tests for voog._security.validate_host — the shared SSRF
defense used by both the MCP voog_list_my_sites tool and the CLI
voog list-my-sites + voog config init --bootstrap-from-token paths.

Token attached to host validation is full-admin scope on the user's
Voog site; rejecting unsafe hosts before the X-API-Token header
leaves the process is the only mitigation.
"""

import unittest

from voog._security import validate_host


class TestValidateHostAcceptsLegitHosts(unittest.TestCase):
    def test_default_voog_admin(self):
        self.assertIsNone(validate_host("www.voog.com", tool_name="t"))

    def test_voog_subdomain(self):
        self.assertIsNone(validate_host("helloworld.voog.com", tool_name="t"))

    def test_tenant_primary_domain(self):
        # Real-world Stella admin API host — must not be rejected.
        self.assertIsNone(validate_host("stellasoomlais.com", tool_name="t"))

    def test_punycode_idn(self):
        # IDN must use punycode to pass the ASCII regex.
        self.assertIsNone(validate_host("xn--example-9zg.com", tool_name="t"))


class TestValidateHostRejectsLocalhost(unittest.TestCase):
    def test_localhost(self):
        self.assertIsNotNone(validate_host("localhost", tool_name="t"))

    def test_ipv6_localhost_name(self):
        self.assertIsNotNone(validate_host("ip6-localhost", tool_name="t"))


class TestValidateHostRejectsRawIPs(unittest.TestCase):
    def test_ipv4_loopback(self):
        self.assertIsNotNone(validate_host("127.0.0.1", tool_name="t"))

    def test_ipv4_rfc1918(self):
        self.assertIsNotNone(validate_host("10.0.0.1", tool_name="t"))
        self.assertIsNotNone(validate_host("192.168.1.1", tool_name="t"))
        self.assertIsNotNone(validate_host("172.16.0.1", tool_name="t"))

    def test_aws_metadata(self):
        self.assertIsNotNone(validate_host("169.254.169.254", tool_name="t"))


class TestValidateHostRejectsPortsAndSchemes(unittest.TestCase):
    def test_embedded_port(self):
        self.assertIsNotNone(validate_host("voog.com:8080", tool_name="t"))

    def test_url_scheme(self):
        self.assertIsNotNone(validate_host("http://voog.com", tool_name="t"))
        self.assertIsNotNone(validate_host("file:///etc/passwd", tool_name="t"))

    def test_userinfo(self):
        self.assertIsNotNone(validate_host("user:pass@voog.com", tool_name="t"))

    def test_path_segment(self):
        self.assertIsNotNone(validate_host("voog.com/admin", tool_name="t"))
        self.assertIsNotNone(validate_host("voog.com?x=1", tool_name="t"))


class TestValidateHostRejectsPrivateTLDs(unittest.TestCase):
    def test_local(self):
        self.assertIsNotNone(validate_host("router.local", tool_name="t"))

    def test_onion(self):
        self.assertIsNotNone(validate_host("evil.onion", tool_name="t"))

    def test_internal(self):
        self.assertIsNotNone(validate_host("staging.internal", tool_name="t"))

    def test_test_reserved(self):
        # `.test` is reserved by RFC 2606
        self.assertIsNotNone(validate_host("foo.test", tool_name="t"))

    def test_example_reserved(self):
        # `.example` is reserved by RFC 2606
        self.assertIsNotNone(validate_host("tenant.example.com", tool_name="t"))


class TestValidateHostRejectsIDNHomograph(unittest.TestCase):
    def test_cyrillic_o(self):
        # Cyrillic о U+043E confusable with Latin o
        self.assertIsNotNone(validate_host("vоog.com", tool_name="t"))


class TestValidateHostRejectsEmptyAndOversized(unittest.TestCase):
    def test_empty(self):
        self.assertIsNotNone(validate_host("", tool_name="t"))

    def test_whitespace(self):
        self.assertIsNotNone(validate_host("   ", tool_name="t"))

    def test_rejects_over_253_octets(self):
        # RFC 1035 bound: 253 chars max for a DNS hostname.
        # Build a maximally-long-yet-DNS-valid string that exceeds
        # the cap. The label cap (63) is a separate concern; here
        # we want the OVERALL length check.
        # Construct: 50 labels of 4 chars each + 49 dots = 249,
        # then add one more label "1234.567" to push to 257.
        long_host = ".".join(["abcd"] * 50) + ".1234567"
        self.assertGreater(len(long_host), 253)
        err = validate_host(long_host, tool_name="t")
        self.assertIsNotNone(err)
        self.assertIn("253", err)

    def test_accepts_exactly_253_octets(self):
        # Boundary check: 253 chars passes the length gate.
        # 50 labels × 4 chars = 200 chars of letters + 49 dots = 249,
        # add ".abc" (4) → 253.
        host = ".".join(["abcd"] * 50) + ".abc"
        self.assertEqual(len(host), 253)
        # May still fail the DNS regex on label structure, but the
        # length gate itself must NOT reject 253.
        err = validate_host(host, tool_name="t")
        if err is not None:
            self.assertNotIn("253", err)


class TestValidateHostErrorMessages(unittest.TestCase):
    def test_tool_name_prefixed(self):
        err = validate_host("localhost", tool_name="voog_list_my_sites")
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith("voog_list_my_sites:"))


if __name__ == "__main__":
    unittest.main()
