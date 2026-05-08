"""Shared helpers for filesystem-touching tool modules.

Six small primitives:

  - :func:`validate_output_dir`        — non-empty + absolute path check;
                                          returns an error string or ``None``.
                                          Both tool groups use the same param
                                          name in their schema (``output_dir``
                                          / ``target_dir``); the label is
                                          passed in.
  - :func:`write_json`                 — write a value as pretty-printed UTF-8
                                          JSON. Centralizes the
                                          indent/ensure_ascii kwargs so disk
                                          artefacts stay byte-identical
                                          across tools (snapshot diffs,
                                          manifests).
  - :func:`_validate_data_key`         — shared validation for user-supplied
                                          data keys interpolated into URL
                                          paths. Rejects empty/whitespace,
                                          ``internal_`` prefix, and characters
                                          / sequences that could alter the URL
                                          structure (``/``, ``?``, ``#``,
                                          ``..``).
  - :func:`validate_translations_shape`— shared shape check for the
                                          ``translations[field]`` payload that
                                          ``product_update`` and
                                          ``ecommerce_settings_update`` both
                                          consume: must be a non-empty
                                          ``dict[str, str]`` with non-empty
                                          values.
  - :func:`require_int`                — rejects bools and non-ints for
                                          ``*_id`` and other integer fields.
                                          Used by T2-T5 (v1.3 pre-release) to
                                          replace the inline
                                          ``isinstance(v, int) and not
                                          isinstance(v, bool)`` pattern that
                                          PR #113 established.
  - :func:`require_force`              — standard force-gate guard for
                                          destructive operations. Used by T5
                                          to replace the 9 inline copies of
                                          ``if not arguments.get("force")``.
"""

import json
import re
import urllib.parse
from pathlib import Path


def _decode_until_stable(s: str, *, max_iter: int = 8) -> str:
    """Iteratively percent-decode ``s`` until it stops changing (or we hit
    ``max_iter``).

    Security invariant: the iteration bound (``max_iter=8``) is intentional —
    bounded iteration prevents pathological inputs from causing DoS, while
    decoding-until-stable defeats double/triple-encoded traversal attempts
    (e.g. ``%252e%252e`` → ``%2e%2e`` → ``..``) that single-pass
    :func:`urllib.parse.unquote` would miss when an upstream proxy normalises
    the percent-escapes a second time before routing.

    Used by both :func:`_validate_data_key` and ``raw.py``'s ``_validate_path``;
    keeping the bound in one place ensures the two validators stay aligned.
    """
    prev = s
    for _ in range(max_iter):
        decoded = urllib.parse.unquote(prev)
        if decoded == prev:
            break
        prev = decoded
    return prev


def validate_output_dir(value: str, *, tool_name: str, param_name: str) -> str | None:
    """Validate that ``value`` is a non-empty absolute path.

    Returns an error message string (suitable for ``error_response``) when
    invalid, or ``None`` when the path is acceptable. Does NOT touch the
    filesystem — callers do their own ``mkdir`` / ``exists`` checks afterwards.
    """
    if not value:
        return f"{tool_name}: {param_name} must be a non-empty string"
    if not Path(value).is_absolute():
        return f"{tool_name}: {param_name} must be an absolute path (got {value!r})"
    return None


def write_json(path: Path, data) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON to ``path``."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def strip_site(arguments: dict) -> dict:
    """Return a copy of arguments without the 'site' key."""
    return {k: v for k, v in arguments.items() if k != "site"}


def validate_translations_shape(field: str, langs, *, tool_name: str) -> str | None:
    """Validate the inner shape of ``translations[field]``.

    Voog expects ``translations[field]`` to be a non-empty mapping of
    ``{lang: value}`` with non-empty string values. The two common LLM
    mistakes this guards against:
      - passing the value as a string directly: ``{"slug": "foo"}``
        (instead of ``{"slug": {"en": "foo"}}``)
      - passing an empty inner dict: ``{"slug": {}}``
      - passing an empty value per lang: ``{"slug": {"et": ""}}``

    Returns an error message string when invalid, ``None`` when acceptable.

    Used by both ``product_update`` and ``ecommerce_settings_update`` so the
    two tools share one shape contract.
    """
    if not isinstance(langs, dict) or not langs:
        return f"{tool_name}: translations[{field!r}] must be a non-empty object {{lang: value}}"
    for lang, value in langs.items():
        if not lang or lang.startswith("-"):
            return f"{tool_name}: empty/malformed lang in translations[{field!r}]: {lang!r}"
        if not value:
            return f"{tool_name}: empty value for translations[{field!r}][{lang!r}]"
    return None


# URL-path-safe allowlist for data keys. Same character class as the
# config-time site-name regex, but with a longer length cap (data keys
# may be longer identifiers than short site names). Rejects spaces,
# unicode, +, @, and other characters that would surface as confusing
# urlopen errors instead of clean validation messages (PR #109 review).
_DATA_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,128}$")


def _validate_data_key(key: str, *, tool_name: str) -> str | None:
    """Validate a user-supplied data key that will be interpolated into a URL path.

    Returns an error message string when the key is invalid, or ``None`` when
    it is acceptable.

    Rejected:
    - empty or whitespace-only
    - keys whose lowercase form starts with ``internal_`` (server-protected
      on Voog; the namespace is treated case-insensitively server-side, so
      ``INTERNAL_x`` and ``Internal_foo`` are rejected too)
    - keys containing ``/``, ``?``, or ``#`` after percent-decoding — these
      would alter URL structure server-side. Apache and many other backends
      normalise ``%2F → /`` before routing, so the structural check has to
      run on the decoded form rather than the raw key (otherwise
      ``foo%2Fbar``, ``foo%23x``, ``foo%3Fx`` slip past).
    - keys that contain ``..`` after percent-decoding (defence-in-depth,
      same hygiene as ``raw.py``'s path validator)
    - keys with characters outside ``[A-Za-z0-9_\\-.]`` or longer than
      128 chars (PR #109 review follow-up — was leaking spaces/unicode/@
      to ``urlopen`` with confusing errors).
    """
    if not key or not key.strip():
        return f"{tool_name}: key must be non-empty"
    # Decode-until-stable so structural checks below see the post-normalisation
    # form — the key is interpolated into a URL path (``/site/data/{key}``),
    # so asymmetric raw-vs-decoded checks were the bypass class.
    decoded = _decode_until_stable(key)
    if decoded.lower().startswith("internal_"):
        return f"{tool_name}: 'internal_' keys are server-protected (got {key!r})"
    for forbidden_char in ("/", "?", "#"):
        if forbidden_char in decoded:
            return f"{tool_name}: key must not contain {forbidden_char!r} (got {key!r})"
    if ".." in decoded.split("/"):
        return f"{tool_name}: key must not contain '..' segments (got {key!r})"
    if not _DATA_KEY_RE.fullmatch(decoded):
        # Echo the decoded form alongside the raw key when they differ
        # (e.g. ``hex%20color`` decodes to ``hex color``) so the caller
        # can see the actual offending character without re-decoding.
        if decoded != key:
            return (
                f"{tool_name}: key must be URL-path-safe — letters/digits/_/-/. "
                f"only, 1-128 chars (got {key!r}, decodes to {decoded!r})"
            )
        return (
            f"{tool_name}: key must be URL-path-safe — letters/digits/_/-/. "
            f"only, 1-128 chars (got {key!r})"
        )
    return None


def require_int(name: str, value, *, tool_name: str) -> str | None:
    """Validate that ``value`` is a plain integer (bools explicitly rejected).

    Returns ``None`` when ``value`` is a valid int and NOT a bool. Returns an
    error message string (suitable for ``error_response``) otherwise.

    Python's ``bool`` is a subclass of ``int``, so ``isinstance(True, int)``
    is ``True``. This helper enforces the PR #113 pattern — explicit bool
    rejection — in one place so callers don't repeat the two-clause check.

    Caller pattern::

        err = require_int("page_id", page_id, tool_name="article_create")
        if err:
            return error_response(err)

    Used by T2-T5 (v1.3 pre-release) to replace the inline
    ``isinstance(v, int) and not isinstance(v, bool)`` copies in
    elements.py, multilingual.py, articles.py, products.py, webhooks.py,
    and others.
    """
    # bool is an int subclass; check first to short-circuit before the int check
    if isinstance(value, bool) or not isinstance(value, int):
        return (
            f"{tool_name}: {name} must be an integer"
            f" (got {type(value).__name__}: {value!r})"
        )
    return None


def require_force(
    arguments: dict,
    *,
    tool_name: str,
    target_desc: str,
    hint: str | None = None,
) -> str | None:
    """Guard a destructive operation behind ``force=true``.

    Returns ``None`` when ``arguments.get("force")`` is truthy (operation
    allowed). Returns a standard error message string when force is absent or
    falsy (operation refused).

    The optional ``hint`` parameter appends a context-specific suggestion to
    the error message (e.g. "Run pages_snapshot first to confirm.").  When
    ``hint`` is ``None`` no trailing text is appended.

    Caller pattern::

        err = require_force(arguments, tool_name="webhook_delete",
                            target_desc=f"webhook {webhook_id}")
        if err:
            return error_response(err)

    Used by T5 (v1.3 pre-release) to replace the 9 inline force-gate copies
    in webhooks.py, elements.py, redirects.py, articles.py, pages_mutate.py,
    multilingual.py, and site.py.

    Precondition: all v1.3 force-gated tools are deletions, so the message
    hardcodes "refusing to delete". If a non-delete force-gated tool is ever
    added, add a ``verb: str = "delete"`` keyword-only parameter and update
    the message template accordingly.
    """
    if arguments.get("force"):
        return None
    msg = (
        f"{tool_name}: refusing to delete {target_desc} without force=true."
        " Set force=true after confirming the deletion is intentional."
    )
    if hint:
        msg = f"{msg} {hint}"
    return msg
