"""voog cart-rules — manage ecommerce cart_rules."""

from __future__ import annotations

import json
import sys

from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser("cart-rules", help="List all cart rules")
    list_p.set_defaults(func=cmd_list)

    get_p = subparsers.add_parser("cart-rule", help="Get a cart rule by id")
    get_p.add_argument("cart_rule_id", type=int)
    get_p.set_defaults(func=cmd_get)

    create_p = subparsers.add_parser(
        "cart-rule-create",
        help=(
            "Create a cart rule. Pass --conditions and --result as JSON. "
            'Example: --conditions=\'[{"value":"100","comparator":">=",'
            '"field":"items_subtotal_amount","value_type":"decimal"}]\''
        ),
    )
    create_p.add_argument("--kind", required=True)
    create_p.add_argument("--target-kind", dest="target_kind", required=True)
    create_p.add_argument("--target-id", dest="target_id", type=int, required=True)
    create_p.add_argument("--conditions", required=True, help="JSON array")
    create_p.add_argument("--result", required=True, help="JSON object")
    create_p.add_argument("--enabled", action="store_true")
    create_p.add_argument("--position", type=int)
    create_p.set_defaults(func=cmd_create)

    update_p = subparsers.add_parser("cart-rule-update", help="Update a cart rule (partial)")
    update_p.add_argument("cart_rule_id", type=int)
    update_p.add_argument("--enabled", choices=["true", "false"])
    update_p.add_argument("--position", type=int)
    update_p.add_argument("--conditions", help="JSON array (replaces all)")
    update_p.add_argument("--result", help="JSON object")
    update_p.set_defaults(func=cmd_update)

    del_p = subparsers.add_parser("cart-rule-delete", help="Delete a cart rule (requires --force)")
    del_p.add_argument("cart_rule_id", type=int)
    del_p.add_argument("--force", action="store_true")
    del_p.set_defaults(func=cmd_delete)


def cmd_list(args, client: VoogClient) -> int:
    rows = client.get_all("/cart_rules", base=client.ecommerce_url)
    print(f"{'ID':<8} {'Pos':<5} {'Enabled':<8} {'Kind':<18} {'Target':<22}")
    print("-" * 70)
    for r in rows:
        target = f"{r.get('target_kind', '')!s}#{r.get('target_id', '')}"
        print(
            f"{r.get('id', ''):<8} {r.get('position', '')!s:<5} "
            f"{str(r.get('enabled', '')):<8} {r.get('kind', '')!s:<18} {target:<22}"
        )
    print(f"\nTotal: {len(rows)} cart rules")
    return 0


def cmd_get(args, client: VoogClient) -> int:
    row = client.get(f"/cart_rules/{args.cart_rule_id}", base=client.ecommerce_url)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_create(args, client: VoogClient) -> int:
    try:
        conditions = json.loads(args.conditions)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"error: --conditions must be valid JSON: {e}\n")
        return 2
    try:
        result_block = json.loads(args.result)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"error: --result must be valid JSON: {e}\n")
        return 2
    body: dict = {
        "kind": args.kind,
        "target_kind": args.target_kind,
        "target_id": args.target_id,
        "conditions": conditions,
        "result": result_block,
    }
    if args.enabled:
        body["enabled"] = True
    if args.position is not None:
        body["position"] = args.position
    result = client.post("/cart_rules", {"cart_rule": body}, base=client.ecommerce_url)
    print(f"  created cart_rule {result.get('id')}")
    return 0


def cmd_update(args, client: VoogClient) -> int:
    body: dict = {}
    if args.enabled is not None:
        body["enabled"] = args.enabled == "true"
    if args.position is not None:
        body["position"] = args.position
    if args.conditions:
        body["conditions"] = json.loads(args.conditions)
    if args.result:
        body["result"] = json.loads(args.result)
    if not body:
        sys.stderr.write("error: supply at least one update field\n")
        return 2
    result = client.put(
        f"/cart_rules/{args.cart_rule_id}",
        {"cart_rule": body},
        base=client.ecommerce_url,
    )
    print(f"  updated cart_rule {result.get('id')}: {sorted(body.keys())}")
    return 0


def cmd_delete(args, client: VoogClient) -> int:
    if not args.force:
        sys.stderr.write(
            f"error: refusing to delete cart_rule {args.cart_rule_id} without --force\n"
        )
        return 2
    client.delete(f"/cart_rules/{args.cart_rule_id}", base=client.ecommerce_url)
    print(f"  deleted cart_rule {args.cart_rule_id}")
    return 0
