"""Shared API payload builders.

Voog's API uses envelope wrappers (e.g. ``{"redirect_rule": {...}}``) and
field naming that occasionally surprises (``destination`` not ``target``).
Centralizing the payload-build keeps CLI and MCP from drifting when Voog
changes its schema.
"""

from __future__ import annotations


def build_redirect_payload(
    source: str,
    destination: str,
    *,
    redirect_type: int = 301,
    active: bool = True,
    regexp: bool = False,
) -> dict:
    """Build the payload for POST /redirect_rules.

    ``regexp`` toggles whether ``source`` is treated as a regex pattern.
    Voog's default for new rules is ``False`` (literal path match);
    callers opt in for pattern-based redirects.
    """
    return {
        "redirect_rule": {
            "source": source,
            "destination": destination,
            "redirect_type": redirect_type,
            "active": active,
            "regexp": regexp,
        }
    }
