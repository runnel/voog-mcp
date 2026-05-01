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
    - keys starting with ``internal_`` (server-protected on Voog)
    - keys containing ``/``, ``?``, or ``#`` — these would alter URL structure
    - keys that contain ``..`` after percent-decoding (defence-in-depth, same
      hygiene as ``raw.py``'s path validator)
    """
    if not key or not key.strip():
        return f"{tool_name}: key must be non-empty"
    if key.startswith("internal_"):
        return f"{tool_name}: 'internal_' keys are server-protected (got {key!r})"
    for forbidden_char in ("/", "?", "#"):
        if forbidden_char in key:
            return f"{tool_name}: key must not contain {forbidden_char!r} (got {key!r})"
    decoded = urllib.parse.unquote(key)
    if ".." in decoded.split("/"):
        return f"{tool_name}: key must not contain '..' segments (got {key!r})"
    return None
