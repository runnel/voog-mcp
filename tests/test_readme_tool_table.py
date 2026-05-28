"""Drift test: every server tool must appear in README.md.

H3 (v1.4 review) caught the README having 30+ unwrapped tools because
Phase 2-4 wrappers landed without README updates. This guard prevents
the same staleness recurring in v1.5+. Same idiom as
``TestEveryToolModuleHasWithToolSweep`` and ``test_no_site_allowlist_drift``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voog.mcp import server

# Server-level tool that is registered separately from TOOL_GROUPS
# (see voog.mcp.server.handle_list_tools). Document it manually here so
# the cross-check still catches drift on the typed surface.
_SERVER_LEVEL_TOOLS = frozenset({"voog_list_sites"})


def _all_server_tools() -> set[str]:
    """Enumerate every typed tool the MCP server registers."""
    names: set[str] = set(_SERVER_LEVEL_TOOLS)
    for group in server.TOOL_GROUPS:
        for tool in group.get_tools():
            names.add(tool.name)
    return names


class TestReadmeToolTableDrift(unittest.TestCase):
    """README.md must enumerate every typed tool the server registers."""

    def setUp(self):
        self.readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
            encoding="utf-8"
        )

    def test_every_server_tool_appears_in_readme(self):
        missing: list[str] = []
        for tool_name in sorted(_all_server_tools()):
            # README uses backtick-wrapped tool names in the table.
            if f"`{tool_name}`" not in self.readme:
                missing.append(tool_name)
        self.assertEqual(
            missing,
            [],
            f"Tool(s) missing from README — sync the tool table before merging: {missing}",
        )

    def test_readme_does_not_list_nonexistent_tool(self):
        # Catch the reverse drift: README claims a tool that doesn't
        # exist (removed wrapper, typo). Scans every backtick-wrapped
        # snake_case identifier inside the "## Tools" section.
        import re

        # Extract just the Tools section so we don't false-positive on
        # body prose that uses backticks for non-tool code (e.g. CLI
        # flags `--include-pii`, attribute names like `force=true`).
        m = re.search(r"## Tools\b.*?(?=^## )", self.readme, flags=re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(m, "README must have a ## Tools section")
        tools_section = m.group(0)

        # Match snake_case-looking backtick tokens, excluding obvious
        # non-tool snippets (flags, kwargs, dotted paths).
        candidates = set(re.findall(r"`([a-z][a-z0-9_]+)`", tools_section))
        actual = _all_server_tools()

        # Allowlist tokens that legitimately appear in the table prose
        # but aren't tool names (e.g. argument names mentioned in
        # parentheses for ``orders_list`` description). Each entry
        # named explicitly so future contributors think before adding.
        prose_allowlist = frozenset(
            {
                "include_pii",  # arg name on orders_list / order_get
                "force",  # arg name on orders + delete tools
                "true",  # boolean literal
                "false",
            }
        )

        unknown = candidates - actual - prose_allowlist
        self.assertEqual(
            unknown,
            set(),
            f"README references tool name(s) that aren't registered: "
            f"{sorted(unknown)} — remove from README or add to "
            f"prose_allowlist if these are non-tool tokens.",
        )


if __name__ == "__main__":
    unittest.main()
