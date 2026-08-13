"""Drift test: every server tool must appear in the endpoint-coverage doc.

``tests/test_readme_tool_table.py`` has guarded README.md both ways since
v1.4. ``docs/voog-mcp-endpoint-coverage.md`` had nothing, and it showed:
``voog_admin_api_read`` and ``voog_ecommerce_api_read`` shipped in v1.4
phase 2 and were still missing from the doc two releases later, while the
doc's own "Everything else" row went on pointing callers at the write
tools for reads. Same idiom as the README guard, on the other document.

Deliberately NOT asserted here: that every tool has its *own row*. The doc
groups by resource — one row can legitimately name six tools — so the
useful invariant is "the doc mentions this tool somewhere", plus the
reverse check that it does not advertise a tool that no longer exists.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from voog.mcp import server

_DOC = Path(__file__).resolve().parent.parent / "docs" / "voog-mcp-endpoint-coverage.md"

# Built-ins registered in server.py itself rather than via TOOL_GROUPS
# (see voog.mcp.server.handle_list_tools). Kept in sync with the identical
# constant in test_readme_tool_table.py.
_SERVER_LEVEL_TOOLS = frozenset({"voog_list_sites", "voog_reload_config"})


def _all_server_tools() -> set[str]:
    names: set[str] = set(_SERVER_LEVEL_TOOLS)
    for group in server.TOOL_GROUPS:
        for tool in group.get_tools():
            names.add(tool.name)
    return names


class TestCoverageDocDrift(unittest.TestCase):
    def setUp(self):
        self.doc = _DOC.read_text(encoding="utf-8")

    def test_every_server_tool_appears_in_the_coverage_doc(self):
        missing = sorted(name for name in _all_server_tools() if f"`{name}`" not in self.doc)
        self.assertEqual(
            missing,
            [],
            "Tool(s) missing from docs/voog-mcp-endpoint-coverage.md — add them "
            f"to the coverage matrix before merging: {missing}",
        )

    # Backticked snake_case identifiers in the doc that are Voog field
    # names, enum values or argument names — NOT MCP tools. This list is
    # exhaustive by construction: anything backticked that is neither a
    # registered tool nor listed here fails the test below. That is
    # deliberately noisier than a name-shape heuristic, which the PR #141
    # review broke by injecting `voog_legacy_api_list` (a tool name that
    # does not exist) and watching the test stay green — the heuristic was
    # blind to 53 of 110 real tool names, so it could not have caught a
    # stale one either.
    NON_TOOL_IDENTIFIERS = frozenset(
        {
            # Voog request/response fields and arguments
            "amount_mode",
            "applies_to",
            "asset_ids",
            "cart_rule",
            "cart_rules",
            "cart_rules_applied",
            "category_id",
            "created_after",
            "created_before",
            "discount_type",
            "element_definition_id",
            "element_definition_title",
            "image_id",
            "items_subtotal_amount",
            "items_total_amount",
            "node_id",
            "page_id",
            "parent_id",
            "parent_node_id",
            "payment_status",
            "products_url_slug",
            "redemption_limit",
            "shipping_method",
            "sizes_complete",
            "target_id",
            "target_ids",
            "target_kind",
            "total_discount_amount",
            "valid_from",
            "valid_to",
            # Voog enum values
            "blog_article",
            "error_401",
            "error_404",
        }
    )

    def test_doc_does_not_advertise_a_tool_that_no_longer_exists(self):
        # Reverse drift: a removed or renamed wrapper still documented.
        real = _all_server_tools()
        candidates = set(re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", self.doc))

        def _documented_as_absent(name: str) -> bool:
            # The doc legitimately names tools that do NOT exist, in order to
            # say so: "node_create / node_delete deferred — not documented by
            # Voog". Flagging those would push the doc toward silence about
            # its own gaps. Only "deferred" earns the exemption — an earlier
            # version also exempted any line containing "passthrough", which
            # covered every row describing the passthrough tools and blinded
            # the check to 25 more names.
            for line in self.doc.splitlines():
                if f"`{name}`" in line and "deferred" in line.lower():
                    return True
            return False

        stale = sorted(
            name
            for name in candidates
            if name not in real
            and name not in self.NON_TOOL_IDENTIFIERS
            and not _documented_as_absent(name)
        )
        self.assertEqual(
            stale,
            [],
            "Coverage doc references name(s) the server does not register. If "
            "these are Voog field names rather than tools, add them to "
            f"NON_TOOL_IDENTIFIERS; otherwise fix the doc: {stale}",
        )

    def test_the_reverse_check_would_catch_an_invented_tool_name(self):
        # Forward-failing guard on the guard. The PR #141 review defeated the
        # previous implementation with exactly this injection.
        real = _all_server_tools()
        for invented in ("voog_legacy_api_list", "page_set_colour", "frobnicate"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, real)
                self.assertNotIn(invented, self.NON_TOOL_IDENTIFIERS)

    def test_footer_verification_date_is_present(self):
        # The doc's value is "this matched Voog on date X"; an undated
        # matrix cannot be audited for staleness.
        self.assertRegex(self.doc, r"Last verified against Voog API: \d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
