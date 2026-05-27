"""MCP error response helpers."""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent


def error_response(
    message: str,
    *,
    details: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> CallToolResult:
    """Return a tool error response as a CallToolResult with isError=True.

    The MCP SDK's call_tool decorator (mcp.server.lowlevel.server.Server.call_tool)
    wraps a list[TextContent] with isError=False — so a tool that simply returns
    list[TextContent] cannot signal a tool-level failure to the client. Returning
    a fully-formed CallToolResult here lets the SDK pass it through untouched
    (handler short-circuits on isinstance(results, CallToolResult)), preserving
    isError=True so clients (Claude included) can distinguish errors from
    successes per spec § 7.

    ``extra`` is folded into the top-level JSON payload alongside the ``error``
    field — used by S15 (layout_delete blocking_pages pre-flight) to surface
    structured supplementary data without polluting the error string.
    ``details`` is the older nested-under-``details`` form, retained for
    callers that prefer the namespaced shape.
    """
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    if extra:
        payload.update(extra)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))],
        isError=True,
    )


def success_response(data: Any, *, summary: str = "") -> list[TextContent]:
    """Return a tool success response with optional human-readable summary."""
    if summary:
        return [
            TextContent(type="text", text=summary),
            TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False)),
        ]
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]
