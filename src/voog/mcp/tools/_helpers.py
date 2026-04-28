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
"""
import json
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
