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
    # non-HTTPS or unexpected hosts to prevent SSRF if Voog API is
    # compromised or returns a malicious URL (e.g. http://169.254.169.254/
    # AWS metadata, or any internal address).
    parsed = urllib.parse.urlparse(upload_url)
    if parsed.scheme != "https":
        raise ValueError(
            f"upload_url failed validation: scheme must be https, got {parsed.scheme!r} "
            f"({upload_url!r})"
        )
    host = (parsed.hostname or "").lower()
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
