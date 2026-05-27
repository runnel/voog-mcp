"""voog orders — list and inspect ecommerce orders (read-only)."""

from __future__ import annotations

import json

from voog._payloads import redact_pii
from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser("orders", help="List orders")
    list_p.add_argument("--status", help="Filter by order status")
    list_p.add_argument("--payment-status", dest="payment_status", help="Filter by payment status")
    list_p.add_argument("--created-after", dest="created_after", help="ISO8601 timestamp")
    list_p.add_argument("--created-before", dest="created_before", help="ISO8601 timestamp")
    list_p.add_argument(
        "--include-pii",
        action="store_true",
        dest="include_pii",
        help="Keep customer email / name / address / phone in the response",
    )
    list_p.set_defaults(func=cmd_list)

    get_p = subparsers.add_parser("order", help="Get a single order by id")
    get_p.add_argument("order_id", type=int)
    get_p.add_argument(
        "--include-pii",
        action="store_true",
        dest="include_pii",
        help="Keep PII in the response",
    )
    get_p.set_defaults(func=cmd_get)


def cmd_list(args, client: VoogClient) -> int:
    params: dict = {}
    if args.status:
        params["q.order.status.$eq"] = args.status
    if args.payment_status:
        params["q.order.payment_status.$eq"] = args.payment_status
    if args.created_after:
        params["q.order.created_at.$gteq"] = args.created_after
    if args.created_before:
        params["q.order.created_at.$lteq"] = args.created_before
    orders = client.get_all("/orders", base=client.ecommerce_url, params=params or None)
    redacted = redact_pii(orders, include_pii=args.include_pii)
    print(json.dumps(redacted, indent=2, ensure_ascii=False))
    suffix = "" if args.include_pii else " (PII stripped)"
    print(f"\nTotal: {len(orders)} orders{suffix}")
    return 0


def cmd_get(args, client: VoogClient) -> int:
    order = client.get(f"/orders/{args.order_id}", base=client.ecommerce_url)
    redacted = redact_pii(order, include_pii=args.include_pii)
    print(json.dumps(redacted, indent=2, ensure_ascii=False))
    return 0
