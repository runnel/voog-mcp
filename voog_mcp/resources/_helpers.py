"""Shared helpers for Phase D resource modules.

After all 5 spec § 5 resource groups landed (PR #19, #20, #21, #23, #24),
``_parse_id`` and ``_json_response`` had been copy-pasted across 4 modules
(pages, layouts, articles, products) with only the group name in error
messages varying. This module centralizes both.

What stays in each resource module:
  - ``URI`` / ``URI_PREFIX`` constant
  - ``get_resources()`` returning the listable Resource(s)
  - ``matches(uri)`` URI ownership check
  - ``read_resource(uri, client)`` dispatch + endpoint calls
  - ``_simplify_*`` projection (group-specific field selection)

What lives here:
  - :func:`parse_id` — strict positive-integer parsing with group-tagged errors
  - :func:`json_response` — wrap data as a single ``application/json`` content

Errors propagate (no wrapping into MCP error responses) — the server layer
turns raised exceptions into JSON-RPC errors.
"""
import json

from mcp.server.lowlevel.helper_types import ReadResourceContents


def parse_id(raw: str, uri: str, *, group_name: str) -> int:
    """Parse a positive integer id from a URI segment.

    Raises ``ValueError`` for non-integer, zero, or negative ids. The error
    message is tagged with ``group_name`` (e.g. ``"pages"``, ``"layouts"``)
    and the full ``uri`` so the failure points to a specific resource shape.

    Both raise paths are explicitly chained:
      - ``from e`` for the ``int()`` conversion failure (preserves traceback)
      - ``from None`` for the value check (no underlying exception to chain;
        the explicit ``None`` suppresses Python's implicit "during handling
        of the above exception" context)
    """
    try:
        parsed = int(raw)
    except ValueError as e:
        raise ValueError(f"{group_name} resource: invalid id in {uri!r}") from e
    if parsed <= 0:
        raise ValueError(f"{group_name} resource: id must be positive in {uri!r}") from None
    return parsed


def json_response(data) -> list[ReadResourceContents]:
    """Wrap ``data`` as a single JSON ``ReadResourceContents``.

    Uses ``indent=2`` and ``ensure_ascii=False`` so non-ASCII content
    (Estonian characters, emojis, etc.) round-trips cleanly without
    \\uXXXX escaping. mime_type is fixed at ``application/json`` —
    callers needing ``text/plain`` (raw .tpl) or ``text/html`` (article body)
    should construct ``ReadResourceContents`` directly.
    """
    return [
        ReadResourceContents(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]
