"""MCP error response helpers."""
import json
from typing import Any
from mcp.types import TextContent


def error_response(message: str, *, details: dict[str, Any] | None = None) -> list[TextContent]:
    """Return a tool error response as TextContent."""
    payload = {"error": message}
    if details:
        payload["details"] = details
    return [TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]


def success_response(data: Any, *, summary: str = "") -> list[TextContent]:
    """Return a tool success response with optional human-readable summary."""
    if summary:
        return [
            TextContent(type="text", text=summary),
            TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False)),
        ]
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]
