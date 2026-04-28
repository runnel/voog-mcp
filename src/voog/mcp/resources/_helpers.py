"""Shared helpers for resource modules.

What stays in each resource module:
  - ``URI`` / ``URI_PREFIX`` constant
  - ``get_resources()`` returning the listable Resource(s)
  - ``matches(uri)`` URI ownership check (built via :func:`prefix_matcher` for
    multi-URI groups)
  - ``read_resource(uri, client)`` dispatch + endpoint calls

Projections (group-specific field selection) live in :mod:`voog_mcp.projections`
since both tools and resources share them.

What lives here:
  - :func:`parse_id`       — strict positive-integer parsing with group-tagged errors
  - :func:`json_response`  — wrap data as a single ``application/json`` content
  - :func:`text_response`  — wrap raw text as a single typed-mime content
                              (e.g. ``text/plain`` for .tpl, ``text/html`` for
                              article body)
  - :func:`prefix_matcher` — closure factory for the
                              ``uri == prefix or uri.startswith(prefix + "/")``
                              ownership check

Encapsulates the SDK-internal ``ReadResourceContents`` import path so resource
modules don't need to reach into ``mcp.server.lowlevel.helper_types`` directly.

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.
"""
import json
from typing import Callable

from mcp.server.lowlevel.helper_types import ReadResourceContents

# Re-exported so resource modules can use the type annotation without
# importing from the SDK's lowlevel namespace themselves.
__all__ = ["ReadResourceContents", "json_response", "parse_id", "prefix_matcher", "text_response"]


def parse_id(raw: str, uri: str, *, group_name: str) -> int:
    """Parse a positive integer id from a URI segment.

    Raises ``ValueError`` for non-integer, zero, or negative ids. The error
    message is tagged with ``group_name`` (e.g. ``"pages"``, ``"layouts"``)
    and the full ``uri`` so the failure points to a specific resource shape.
    """
    try:
        parsed = int(raw)
    except ValueError as e:
        raise ValueError(f"{group_name} resource: invalid id in {uri!r}") from e
    if parsed <= 0:
        raise ValueError(f"{group_name} resource: id must be positive in {uri!r}") from None
    return parsed


def prefix_matcher(prefix: str) -> Callable[[str], bool]:
    """Return a ``matches(uri)`` predicate for a multi-URI resource group.

    The closure accepts ``uri == prefix`` (the listable root) and
    ``uri.startswith(prefix + "/")`` (per-id sub-paths) — the trailing
    slash check is what stops ``voog://pagesx`` from being silently
    claimed by a ``voog://pages`` group.
    """
    sub_prefix = prefix + "/"

    def matches(uri: str) -> bool:
        return uri == prefix or uri.startswith(sub_prefix)

    return matches


def json_response(data) -> list[ReadResourceContents]:
    """Wrap ``data`` as a single JSON ``ReadResourceContents``.

    Uses ``indent=2`` and ``ensure_ascii=False`` so non-ASCII content
    (Estonian characters, emojis, etc.) round-trips cleanly without
    \\uXXXX escaping.
    """
    return [
        ReadResourceContents(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]


def text_response(content: str, *, mime_type: str) -> list[ReadResourceContents]:
    """Wrap a raw string body as a single ``ReadResourceContents``.

    For non-JSON resources: ``text/plain`` (raw .tpl source) and
    ``text/html`` (article body). Centralizes the
    ``ReadResourceContents`` import so resource modules don't depend on
    SDK-internal paths.
    """
    return [ReadResourceContents(content=content, mime_type=mime_type)]
