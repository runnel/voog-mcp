"""Shared helpers for filesystem-touching tool modules.

Four small primitives:

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
"""

import json
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
    return None
