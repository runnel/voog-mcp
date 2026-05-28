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
        # Defensive: `extra` flattens into the top-level payload, so a caller
        # passing `extra={"error": "..."}` or `extra={"details": ...}` would
        # silently clobber the canonical fields. Refuse the call instead.
        overlap = set(extra) & {"error", "details"}
        if overlap:
            raise ValueError(
                f"error_response: extra kwarg cannot override "
                f"{sorted(overlap)}; use a distinct top-level key"
            )
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


class RequestBudgetExceeded(RuntimeError):
    """Raised when a single VoogClient exceeds its per-instance request cap.

    The cap is set via the ``VOOG_REQUEST_CAP`` environment variable
    (default 5000) and counts successful HTTP requests over the lifetime
    of a single :class:`VoogClient` instance. Retries within one logical
    call count as a single request (per R8): the counter is incremented
    once on the response, not once per attempt.

    Snapshot tool (Phase 5 MD4) catches this exception, finalises the
    ``_meta.json`` manifest with ``aborted_reason="request_budget_exceeded"``,
    then re-raises so the operator sees the failure.
    """


class DailyQuotaExceeded(RuntimeError):
    """Raised when a site's daily request quota (configured via
    ``daily_request_quota`` in ``voog.json``) is exhausted for the
    current UTC day.

    The counter is persisted to
    ``platformdirs.user_cache_dir("voog-mcp")/quota.json`` and reset at
    UTC midnight. Snapshot tool (Phase 5 MD4) catches this exception,
    finalises ``_meta.json`` with
    ``aborted_reason="daily_quota_exceeded"``, then re-raises.
    """
