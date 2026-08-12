"""Shared manifest-push logic for ``voog push`` (CLI) and ``layouts_push`` (MCP).

Both callers walk a pulled tree's ``manifest.json`` and PUT each tracked file
back to Voog. Three things must not drift between them: which endpoint an
entry type routes to, which payload field carries the content, and how a
"200 OK but nothing persisted" response is detected. Issue #96 found the
silent no-op on the CLI side, #99 mirrored the detector into MCP by copying
it, and #75 established the rule that duplicated CLI/MCP payload logic gets
extracted rather than kept in sync by hand. This module is the single
definition; #138 added the asset route to the MCP side by importing it.

Both endpoints take a flat payload — wrapping ``{"layout_asset": …}`` is
silently 200-ed without persisting (issue #96).
"""

from __future__ import annotations

from datetime import datetime

# Endpoint dispatch by manifest entry type → (path prefix, content field).
# ``layout_asset`` is the legacy spelling written by pre-rename ``voog.py``
# manifests; current ``voog pull`` emits ``asset``. Routing both to the same
# target keeps long-lived checkouts working without a forced re-pull.
ENDPOINT_BY_TYPE = {
    "layout": ("/layouts", "body"),
    "asset": ("/layout_assets", "data"),
    "layout_asset": ("/layout_assets", "data"),
}

ASSET_TYPES = frozenset({"asset", "layout_asset"})


def verify_persisted(kind: str, body: str, entry: dict, result) -> str | None:
    """Return an error message if the PUT response contradicts a successful
    persist, else None. Voog's PUT responses are slim — the content field
    is omitted, so we rely on indirect signals: `size` for assets and
    `updated_at` for layouts. Each check falls through when the signal is
    missing from the response (or, for layouts, from the manifest), so we
    don't false-positive against older endpoints / hand-crafted manifests.

    Note this proves *persistence*, not *fidelity*: it compares what Voog
    stored against what this process sent. Content mangled before it reached
    the process (issue #138 — a JSON transport decoding literal ``\\uXXXX``
    escapes) passes cleanly, which is exactly why pushing from disk is the
    byte-exact path.
    """
    if not isinstance(result, dict):
        return None
    if kind in ASSET_TYPES:
        # Voog's `size` field counts UTF-8 *characters*, not bytes —
        # empirically verified post-1.2.1 release (any file with a
        # non-ASCII char like an em-dash or Estonian õ otherwise produced
        # a false-positive ✗).  Compare against str length, not the
        # encoded byte count.
        sent_chars = len(body)
        stored_size = result.get("size")
        if stored_size is not None and stored_size != sent_chars:
            return (
                f"stored size {stored_size} does not match local "
                f"{sent_chars} characters — content NOT updated on Voog"
            )
    elif kind == "layout":
        prev = parse_iso8601(entry.get("updated_at"))
        new = parse_iso8601(result.get("updated_at"))
        if prev and new and new <= prev:
            return (
                f"updated_at did not advance ({result.get('updated_at')}) — "
                "content NOT updated on Voog"
            )
    return None


def parse_iso8601(value) -> datetime | None:
    """Best-effort ISO 8601 parse. Voog returns timestamps like
    ``2026-05-01T10:01:17.806Z``. Returns None on anything we can't parse —
    callers must treat None as "no signal" and fall through rather than
    flagging a no-op. Avoids string comparison foot-guns when the server
    and the manifest disagree on fractional-second precision.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
