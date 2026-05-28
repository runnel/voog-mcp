"""SSRF defense for Voog's 3-step asset upload protocol.

The protocol's step 2 sends raw file bytes to ``upload_url``, a value
returned by Voog's POST /assets response. A compromised or misbehaving
Voog API could redirect that PUT to an internal address (AWS metadata
at 169.254.169.254, RFC1918 ranges) or downgrade to HTTP. This module
is the single trust-boundary check shared by both callers (the MCP
tool and the CLI command) so the two cannot drift.

Both callers must invoke :func:`_validate_upload_url` *after* the
POST /assets that mints ``asset_id`` — a failure here means the asset
record is already an orphan in Voog's library and the caller is
responsible for surfacing recovery guidance.
"""

import os
import urllib.parse

# Allowlist of host suffixes for the presigned upload_url returned by Voog's
# POST /assets. Voog uploads land on S3 today, so the default is just
# *.amazonaws.com — narrow on purpose, since `.voog.com` would let a
# misbehaving API steer the file at a Voog admin endpoint instead of an S3
# bucket. Override at deploy time via VOOG_UPLOAD_HOST_SUFFIXES (comma-
# separated, e.g. "amazonaws.com,voogcdn.com") if Voog migrates the upload
# host. Leading dot is optional in env input — entries are matched as bare
# host or dot-boundary suffix (so "evil.com" never matches "notevil.com").
_DEFAULT_UPLOAD_HOST_SUFFIXES = ("amazonaws.com",)


def _allowed_upload_host_suffixes() -> tuple[str, ...]:
    raw = os.environ.get("VOOG_UPLOAD_HOST_SUFFIXES")
    if not raw:
        return _DEFAULT_UPLOAD_HOST_SUFFIXES
    # Strip leading dots — matching adds the boundary itself, so the input
    # format is forgiving (".amazonaws.com" and "amazonaws.com" both work).
    parts = tuple(p.strip().lstrip(".") for p in raw.split(",") if p.strip().lstrip("."))
    return parts or _DEFAULT_UPLOAD_HOST_SUFFIXES


def _validate_upload_url(upload_url: str) -> None:
    # Trust boundary: upload_url comes from the Voog API response. Refuse
    # non-HTTPS, userinfo prefixes, unexpected hosts, or IDN homographs of
    # an expected host. The threat model assumes Voog API may be
    # compromised or buggy and could return:
    #   - http:// to downgrade the upload PUT
    #   - a userinfo prefix (https://attacker@s3.amazonaws.com/...) that
    #     confuses downstream auditing about the request origin
    #   - an internal address (169.254.169.254 AWS metadata, RFC1918)
    #   - a unicode look-alike (https://аmazonaws.com/ with cyrillic а)
    parsed = urllib.parse.urlparse(upload_url)

    if parsed.scheme != "https":
        raise ValueError(
            f"upload_url failed validation: scheme must be https, got {parsed.scheme!r} "
            f"({upload_url!r})"
        )

    # urlparse exposes credentials via .username / .password. Either being
    # set means the URL carries a userinfo prefix — refuse, the legitimate
    # presigned-S3 flow never uses one.
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            f"upload_url failed validation: URL must not contain userinfo "
            f"(found username={parsed.username!r}) ({upload_url!r})"
        )

    host_raw = (parsed.hostname or "").lower()

    # IDN homograph defense: normalise the host to its ASCII (Punycode)
    # form before matching against the allowlist. A cyrillic "а" in
    # "аmazonaws.com" encodes to "xn--mazonaws-7l4d.com" — different
    # ASCII string, fails the suffix match, raises here rather than
    # silently uploading to an attacker-controlled host.
    try:
        host = host_raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        # Empty host, host too long, or unencodable characters — refuse.
        raise ValueError(
            f"upload_url failed validation: host {host_raw!r} could not be "
            f"normalised to ASCII (IDN/Punycode error: {exc}) ({upload_url!r})"
        ) from exc

    suffixes = _allowed_upload_host_suffixes()
    # Match bare host (host == "amazonaws.com") OR dot-boundary suffix
    # (host endswith ".amazonaws.com") — never a substring endswith, so
    # "notevil.com" cannot match an "evil.com" entry.
    if not any(host == s or host.endswith("." + s) for s in suffixes):
        raise ValueError(
            f"upload_url failed validation: host {host!r} not in allowlist "
            f"{suffixes} (set VOOG_UPLOAD_HOST_SUFFIXES to override) "
            f"({upload_url!r})"
        )
