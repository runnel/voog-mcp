"""Trust-boundary checks for uploads: where bytes go, and which bytes go.

Two halves:

  - :func:`_validate_upload_url` — SSRF defense on the destination (below).
  - :func:`validate_upload_source` — vetting of the local file an upload
    tool was pointed at. Tool arguments reach these tools from an LLM, and
    an upload's result is *published* (``/photos/…``, ``/images/…``), so a
    mis-aimed path is an exfiltration primitive, not just a bad request.
    The extension allowlist alone is not that check: it reads
    ``path.suffix``, which a symlink (``innocent.jpg`` → an SSH key)
    trivially defeats. Content is therefore matched against the extension's
    magic bytes, and the size is capped.

SSRF defense for Voog's 3-step asset upload protocol.

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
from pathlib import Path

# Allowlist of host suffixes for the presigned upload_url returned by Voog's
# POST /assets:
#   - media.voog.com — what Voog actually hands out (issue #137). The S3
#     presigned query string (AWSAccessKeyId / Expires / Signature) is
#     intact; only the host is CNAME'd. Probed live 2026-08-12.
#   - amazonaws.com — bare S3, kept for tenants/regions served without the
#     CNAME and for the pre-#137 contract.
# Deliberately NOT a blanket `.voog.com`: that would let a misbehaving API
# steer the file at a Voog admin endpoint instead of the media store. Admin
# traffic lives on the tenant host (where confirm_url points) and on
# www.voog.com — neither matches a `media.voog.com` dot-boundary suffix.
# Override at deploy time via VOOG_UPLOAD_HOST_SUFFIXES (comma-separated,
# e.g. "amazonaws.com,voogcdn.com") if Voog migrates the upload host again.
# Leading dot is optional in env input — entries are matched as bare host or
# dot-boundary suffix (so "evil.com" never matches "notevil.com").
_DEFAULT_UPLOAD_HOST_SUFFIXES = ("amazonaws.com", "media.voog.com")


# Upper bound on a single uploaded file. Voog's own free-plan asset quota is
# ~100 MB for the WHOLE site, so anything approaching this is already a
# mistake; the cap exists so a mis-aimed path (a disk image, a database
# dump) fails fast instead of streaming to a public URL.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Leading bytes that must be present for a given extension. An entry means
# "if the caller claims this extension, the content has to look like it".
# Extensions absent from this map (.eot, .svg — text, no fixed signature)
# are accepted on extension alone.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".ico": (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"),
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
    ".ttf": (b"\x00\x01\x00\x00", b"true", b"ttcf"),
    ".otf": (b"OTTO", b"\x00\x01\x00\x00"),
    ".pdf": (b"%PDF",),
}


def validate_upload_source(path: Path, allowed: dict, *, tool_name: str) -> str | None:
    """Vet a local file before an upload tool reads and publishes it.

    Returns an error message, or None when the file is acceptable. Checks,
    in order: it exists and is a regular file; its extension is in
    ``allowed``; it is within :data:`MAX_UPLOAD_BYTES`; and its leading
    bytes match the extension.

    The content check is what makes the extension allowlist mean anything.
    ``path.suffix`` describes the *name*, so a symlink or a renamed file
    passes it while carrying something else entirely — and because uploads
    land on a public URL, that turns a wrong path into published data.
    """
    if not path.is_file():
        return f"{tool_name}: {str(path)!r} does not exist (or is not a regular file)"
    suffix = path.suffix.lower()
    if suffix not in allowed:
        return (
            f"{tool_name}: {path.name!r} has unsupported type {path.suffix!r}; "
            f"supported: {', '.join(sorted(allowed))}"
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"{tool_name}: cannot stat {str(path)!r}: {exc}"
    if size == 0:
        return f"{tool_name}: {str(path)!r} is empty"
    if size > MAX_UPLOAD_BYTES:
        return (
            f"{tool_name}: {path.name!r} is {size} bytes, over the "
            f"{MAX_UPLOAD_BYTES}-byte upload cap"
        )
    expected = _MAGIC_PREFIXES.get(suffix)
    if expected:
        try:
            with path.open("rb") as fh:
                head = fh.read(16)
        except OSError as exc:
            return f"{tool_name}: cannot read {str(path)!r}: {exc}"
        if not any(head.startswith(prefix) for prefix in expected):
            return (
                f"{tool_name}: {str(path)!r} does not contain {suffix} data "
                f"(leading bytes {head[:8]!r}). The extension is not proof of "
                "content — a symlink or a renamed file would otherwise be "
                "uploaded to a public URL."
            )
    return None


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
