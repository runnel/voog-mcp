"""Shared helpers for filesystem-touching tool modules.

Three small primitives used by both ``snapshot.py`` and ``layouts_sync.py``:

  - :func:`validate_output_dir`  — non-empty + absolute path check; returns an
                                    error string or ``None``. Both tool groups
                                    use the same param name in their schema
                                    (``output_dir`` / ``target_dir``); the
                                    label is passed in.
  - :func:`write_json`           — write a value as pretty-printed UTF-8 JSON.
                                    Centralizes the indent/ensure_ascii kwargs
                                    so disk artefacts stay byte-identical
                                    across tools (snapshot diffs, manifests).
  - :func:`_validate_data_key`   — shared validation for user-supplied data
                                    keys interpolated into URL paths. Rejects
                                    empty/whitespace, ``internal_`` prefix, and
                                    characters / sequences that could alter the
                                    URL structure (``/``, ``?``, ``#``, ``..``).
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
    # Decode at the top so every structural check below sees the
    # post-normalisation form. Asymmetric checks (raw vs decoded) were
    # the bypass class. Loop until stable: the key is interpolated into
    # a URL path (``/site/data/{key}``) so the same double-decode threat
    # model as ``raw.py``'s path validator applies.
    decoded = key
    for _ in range(8):
        next_decoded = urllib.parse.unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if decoded.lower().startswith("internal_"):
        return f"{tool_name}: 'internal_' keys are server-protected (got {key!r})"
    for forbidden_char in ("/", "?", "#"):
        if forbidden_char in decoded:
            return f"{tool_name}: key must not contain {forbidden_char!r} (got {key!r})"
    if ".." in decoded.split("/"):
        return f"{tool_name}: key must not contain '..' segments (got {key!r})"
    return None
