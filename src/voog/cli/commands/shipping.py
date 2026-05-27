"""voog shipping — list shipping methods + gateways (read-only)."""

from __future__ import annotations

import json

from voog.client import VoogClient


def add_arguments(subparsers):
    sm_p = subparsers.add_parser("shipping-methods", help="List all shipping methods")
    sm_p.add_argument(
        "--with-options",
        action="store_true",
        dest="with_options",
        help="Include the full options[] list (parcel machine locations)",
    )
    sm_p.set_defaults(func=cmd_shipping_methods)

    gw_p = subparsers.add_parser("gateways", help="List all payment gateways")
    gw_p.set_defaults(func=cmd_gateways)


def cmd_shipping_methods(args, client: VoogClient) -> int:
    rows = client.get_all("/shipping_methods", base=client.ecommerce_url)
    if not args.with_options:
        # Skip the options[] tree by default — it's noisy for Omniva-style
        # parcel-machine carriers (one list per method, sometimes 500+ entries).
        rows = [{k: v for k, v in r.items() if k != "options"} for r in rows]
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(rows)} shipping methods")
    return 0


def cmd_gateways(args, client: VoogClient) -> int:
    rows = client.get_all("/gateways", base=client.ecommerce_url)
    print(f"{'Code':<22} {'Enabled':<8} Name")
    print("-" * 70)
    for r in rows:
        print(f"{r.get('code', '')!s:<22} {str(r.get('enabled', '')):<8} {r.get('name', '')}")
    print(f"\nTotal: {len(rows)} gateways")
    return 0
