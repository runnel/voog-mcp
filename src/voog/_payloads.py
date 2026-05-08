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


def build_settings_payload(body: dict) -> dict:
    """Wrap a prepared ecommerce settings body in the ``{"settings": {...}}`` envelope.

    Mirror of ``build_product_payload``: the caller assembles and
    validates ``body`` (attribute whitelist, translations shape, etc.);
    this builder just wraps it in the API envelope.

    Voog ecommerce v1 accepts ``{"settings": {...}}`` for PUT /settings.
    Centralising the envelope here means future Voog schema changes only
    need to touch one place rather than every caller of `_settings`.
    """
    return {"settings": dict(body)}


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

    Use ``build_redirect_envelope`` for the "I already have a prepared
    body" case (e.g. PUT /redirect_rules/{id} with a merged dict).
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


def build_redirect_envelope(body: dict) -> dict:
    """Wrap a prepared redirect_rule body in the ``{"redirect_rule": {...}}`` envelope.

    Mirror of ``build_product_payload`` / ``build_settings_payload``:
    the caller assembles ``body`` (e.g. via the GET-merge-PUT path in
    ``_redirect_update``) and this builder just wraps it. Use this when
    you already have the full dict; for fresh rule construction with
    keyword args, prefer ``build_redirect_payload``.
    """
    return {"redirect_rule": dict(body)}


# Article field mapping. The three autosaved_* keys are the writable
# pair to article.title/body/excerpt (read-only on the public side per
# Voog convention). Pass-through keys are non-autosaved, written
# directly to article.<field>.
_ARTICLE_AUTOSAVED_MAP = {
    "title": "autosaved_title",
    "body": "autosaved_body",
    "excerpt": "autosaved_excerpt",
}
_ARTICLE_PASSTHROUGH = ("description", "path", "image_id", "tag_names", "data")


def build_article_payload(arguments: dict, *, include_publish: bool = False) -> dict:
    """Build the FLAT body for POST/PUT /articles.

    Articles use a flat body (no envelope wrapper) but require the
    autosaved_* convention: ``article.title`` is read-only, writes go
    to ``autosaved_title``. Same for ``body`` and ``excerpt``. Other
    fields (``description``, ``path``, ``image_id``, ``tag_names``,
    ``data``) are pass-through.

    Only keys that are explicitly present and not None are included —
    empty strings/lists ARE included. Missing keys are simply absent.

    ``include_publish=True`` (POST-only) maps the caller's truthy
    ``arguments["publish"]`` to ``"publishing": True``. ``publish=False``
    is treated as absence (no ``publishing`` key emitted).
    """
    body: dict = {}
    for arg_key, body_key in _ARTICLE_AUTOSAVED_MAP.items():
        if arguments.get(arg_key) is not None:
            body[body_key] = arguments[arg_key]
    for key in _ARTICLE_PASSTHROUGH:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if include_publish and arguments.get("publish"):
        body["publishing"] = True
    return body
