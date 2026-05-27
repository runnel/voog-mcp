"""Shared security validators for host / URL inputs.

Sibling of ``voog._upload_validation`` (which validates URLs from Voog's
API responses — trust boundary is the Voog API). This module validates
hosts supplied by the LLM (MCP tool) or operator (CLI) — trust boundary
is user/LLM input.

Single trust-boundary check shared by all callers so the two surfaces
cannot drift. Lifted from ``voog.mcp.tools.me`` in PR #125 pass-2
review so the CLI `voog list-my-sites` and `voog config init
--bootstrap-from-token` paths get the same defense as the MCP tool.
"""

import ipaddress
import re

# DNS-name shape. Blocks IDN homograph attacks because non-ASCII codepoints
# don't match the regex — IDN callers must use punycode (xn--…).
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")

# Private-use / reserved TLDs that no legitimate Voog tenant would use.
_PRIVATE_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".intranet",
    ".onion",
    ".lan",
    ".home",
    ".corp",
    ".test",
    ".example",
    ".invalid",
    ".localhost",
)

# Loopback / reserved hostnames that bypass the TLD denylist.
_REJECTED_HOSTS = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
    }
)

# RFC 2606 reserved second-level domains (documentation-only). Rejected
# both as bare apex and as any subdomain. The TLD list above handles
# `.example`, `.test`, etc.; this catches the equally-canonical
# `example.com` / `.net` / `.org` form that gets typo'd into prod
# configs and prompts.
_RESERVED_DOMAINS = (
    "example.com",
    "example.net",
    "example.org",
)

# RFC 1035 caps DNS hostnames at 253 octets. This bound is also a cheap
# defense against absurdly large inputs reaching the regex / ipaddress
# parser (no ReDoS in this regex, but a 1MB string still costs an O(n)
# pass through every check downstream).
_MAX_HOSTNAME_LENGTH = 253


def validate_host(host: str, *, tool_name: str) -> str | None:
    """SSRF-defensive validation for an LLM-/operator-supplied host.

    Returns an error message string (suitable for ``error_response`` or
    a CLI stderr write) when the host should be rejected; returns
    ``None`` when the host is acceptable. ``tool_name`` is prefixed
    into the error message for caller-specific context.

    Rejected classes:
      - empty / whitespace
      - length > 253 (RFC 1035 cap)
      - explicit ports (host:8080) — Voog admin is HTTPS:443 only;
        an embedded port is a strong signal of redirection
      - userinfo (user@host) and credentials in URL form
      - scheme prefix (`http://`, `https://`, etc.) — host must be bare
      - localhost and loopback hostnames
      - raw IPv4 / IPv6 addresses, including loopback / private /
        link-local / reserved ranges (incl. AWS metadata 169.254.169.254)
      - private-use TLDs (.local, .internal, .intranet, .onion, .test,
        .example, .invalid, .localhost, .lan, .home, .corp)
      - non-DNS characters (anything outside ``[a-z0-9.-]`` after
        lowercasing — covers Unicode IDN homograph attacks)

    NOT a `*.voog.com` allowlist — tenants legitimately host the admin
    API on their own primary domain (e.g. ``stellasoomlais.com``), so
    a domain allowlist would reject real-world setups.
    """
    if not host or not host.strip():
        return f"{tool_name}: host must be non-empty"
    if len(host) > _MAX_HOSTNAME_LENGTH:
        return (
            f"{tool_name}: host longer than RFC 1035 max "
            f"({len(host)} > {_MAX_HOSTNAME_LENGTH} octets)"
        )
    h = host.strip().lower()

    # Scheme / userinfo / port — none of these belong in a bare hostname.
    if "://" in h:
        return f"{tool_name}: host must be bare (no scheme), got {host!r}"
    if "@" in h:
        return f"{tool_name}: host must not contain '@' (no userinfo), got {host!r}"
    if "/" in h or "?" in h or "#" in h:
        return f"{tool_name}: host must be a bare hostname (no path), got {host!r}"
    if ":" in h:
        # IPv6 in brackets or explicit port — reject both. Voog admin
        # is always HTTPS:443.
        return f"{tool_name}: host must not contain ':' (no port / IPv6), got {host!r}"

    # Loopback / well-known local names.
    if h in _REJECTED_HOSTS:
        return f"{tool_name}: host {host!r} is a loopback / reserved name"

    # Raw IPv4 — reject regardless of range. Tenants identify by
    # hostname, not by IP; an IP in this slot is exclusively an
    # attempt to bypass DNS-based defenses.
    try:
        ipaddress.ip_address(h)
        return f"{tool_name}: host {host!r} is a raw IP address; use a hostname"
    except ValueError:
        pass

    # Private-use TLDs.
    for suffix in _PRIVATE_HOST_SUFFIXES:
        if h.endswith(suffix) or h == suffix.lstrip("."):
            return f"{tool_name}: host {host!r} uses a private / reserved TLD ({suffix})"

    # RFC 2606 reserved second-level domains (example.com / .net / .org).
    # Catch both bare apex and subdomains — `example.com`, `foo.example.com`.
    for reserved in _RESERVED_DOMAINS:
        if h == reserved or h.endswith("." + reserved):
            return (
                f"{tool_name}: host {host!r} uses an RFC 2606 reserved "
                f"second-level domain ({reserved})"
            )

    # DNS-name shape (allowed chars + label structure). Blocks IDN
    # homograph attacks; punycode callers can still pass.
    if not _HOSTNAME_RE.match(h):
        return (
            f"{tool_name}: host {host!r} is not a valid DNS hostname "
            "(use lowercase letters, digits, '.', '-' only; "
            "punycode for IDN)"
        )

    return None
