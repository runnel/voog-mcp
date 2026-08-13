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

    def test_doc_does_not_advertise_a_tool_that_no_longer_exists(self):
        # Reverse drift: a removed or renamed wrapper still documented.
        # Scan backtick-wrapped snake_case identifiers that look like tool
        # names, and ignore prose that happens to use the same shape.
        real = _all_server_tools()
        candidates = set(re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", self.doc))
        # Names in the doc that are Voog fields / CLI flags / helper
        # functions rather than MCP tools. Anything not listed here and not
        # a real tool fails, so a genuinely stale tool name cannot hide.
        known_non_tools = {
            "api_key_env",
            "asset_ids",
            "cart_rules_applied",
            "content_type",
            "created_at",
            "daily_request_quota",
            "default_site",
            "discount_objects",
            "effective_price",
            "element_definition_id",
            "element_definition_title",
            "element_definitions",
            "external_shipment_attrs",
            "gateway_transaction_id",
            "image_id",
            "in_stock",
            "include_body",
            "include_pii",
            "internal_trial_assets_quota",
            "is_spam",
            "items_subtotal_amount",
            "items_total_amount",
            "language_code",
            "layout_id",
            "max_workers",
            "media_set",
            "menu_title",
            "meta_keywords",
            "on_sale",
            "output_dir",
            "page_id",
            "parent_id",
            "parent_node_id",
            "per_page",
            "price_entry_mode",
            "price_max",
            "price_min",
            "primary_domain",
            "processed_ids",
            "failed_ids",
            "products_url_slug",
            "public_url",
            "redirect_type",
            "return_url",
            "shipping_method",
            "shipping_method_option",
            "sitemap_enabled",
            "sizes_complete",
            "state_dir",
            "target_ids",
            "tag_names",
            "total_discount_amount",
            "updated_at",
            "uses_variants",
            "valid_from",
            "valid_to",
            "variant_attributes",
            "variant_types",
            "voog_mcp_version",
            "redemption_limit",
            "amount_mode",
            "discount_type",
            "applies_to",
            "target_kind",
            "target_id",
            "value_type",
            "enabled_methods",
            "all_payment_methods",
            "physical_properties",
            "data_usage",
            "source_field",
            "target_field",
            "aborted_reason",
            "allow_duplicate",
            "wait_for_sizes",
            "order_verified",
            "stored_asset_ids",
            "asset_type",
            "content_partials",
            "layout_assets",
            "redirect_rules",
            "media_sets",
            "element_definitions_list",
        }

        def _looks_like_a_tool_name(name: str) -> bool:
            # Tool names in this package carry the `voog_` prefix or end in a
            # verb segment; Voog field names (`asset_ids`, `valid_from`) do
            # neither, so this keeps prose out of the check.
            return name.startswith("voog_") or name.endswith(
                ("_list", "_get", "_create", "_update", "_delete", "_snapshot")
            )

        def _documented_as_absent(name: str) -> bool:
            # The doc legitimately names tools that do NOT exist, to say so:
            # "node_create / node_delete deferred — not documented by Voog".
            # Flagging those would push the doc toward silence about the
            # gaps, which is the opposite of what it is for.
            for line in self.doc.splitlines():
                if f"`{name}`" in line and (
                    "deferred" in line.lower() or "passthrough" in line.lower()
                ):
                    return True
            return False

        stale = sorted(
            name
            for name in candidates
            if name not in real
            and name not in known_non_tools
            and _looks_like_a_tool_name(name)
            and not _documented_as_absent(name)
        )
        self.assertEqual(
            stale,
            [],
            f"Coverage doc references tool(s) that the server does not register: {stale}",
        )

    def test_footer_verification_date_is_present(self):
        # The doc's value is "this matched Voog on date X"; an undated
        # matrix cannot be audited for staleness.
        self.assertRegex(self.doc, r"Last verified against Voog API: \d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
