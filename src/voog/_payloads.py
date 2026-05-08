"""Shared API payload builders.

Voog's API uses envelope wrappers (e.g. ``{"redirect_rule": {...}}``) and
field naming that occasionally surprises (``destination`` not ``target``).
Centralizing the payload-build keeps CLI and MCP from drifting when Voog
changes its schema.
"""

from __future__ import annotations


def build_product_payload(body: dict) -> dict:
    """Wrap a prepared product body in the Voog ``{"product": {...}}`` envelope.

    The caller (``_product_update``, ``_product_create``, CLI ``product``)
    is responsible for assembling and validating ``body`` — running the
    whitelist check, status enum check, asset_ids translation,
    translations folding, etc. This builder is a pure wrapper that
    centralises the envelope shape so future Voog API changes only need
    to touch one place.

    Voog accepts ``{"product": {...}}`` for both POST and PUT.
    """
    return {"product": dict(body)}


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
