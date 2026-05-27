"""voog discounts — manage ecommerce discounts."""

from __future__ import annotations

import json
import sys

from voog.client import VoogClient

# Fields that can be passed via --field=value pairs on create/update.
WRITABLE = (
    "code",
    "name",
    "description",
    "amount",
    "amount_mode",
    "discount_type",
    "status",
    "applies_to",
    "valid_from",
    "valid_to",
    "redemption_limit",
    "stackable",
    "currency",
)


def add_arguments(subparsers):
    list_p = subparsers.add_parser("discounts", help="List all discounts")
    list_p.set_defaults(func=cmd_list)

    get_p = subparsers.add_parser("discount", help="Get a discount by id")
    get_p.add_argument("discount_id", type=int)
    get_p.set_defaults(func=cmd_get)

    create_p = subparsers.add_parser("discount-create", help="Create a discount")
    create_p.add_argument("code")
    create_p.add_argument(
        "fields",
        nargs="*",
        help="key=value pairs, e.g. amount=10 amount_mode=net discount_type=percentage status=open applies_to=cart",
    )
    create_p.set_defaults(func=cmd_create)

    update_p = subparsers.add_parser("discount-update", help="Update a discount")
    update_p.add_argument("discount_id", type=int)
    update_p.add_argument("fields", nargs="*", help="key=value pairs")
    update_p.set_defaults(func=cmd_update)

    del_p = subparsers.add_parser("discount-delete", help="Delete a discount (requires --force)")
    del_p.add_argument("discount_id", type=int)
    del_p.add_argument("--force", action="store_true")
    del_p.set_defaults(func=cmd_delete)


def _parse_kv_pairs(items: list[str]) -> dict:
    body: dict = {}
    for it in items:
        if "=" not in it:
            sys.stderr.write(f"error: expected key=value, got {it!r}\n")
            sys.exit(2)
        key, val = it.split("=", 1)
        if key not in WRITABLE:
            sys.stderr.write(f"error: unknown field {key!r}; allowed: {list(WRITABLE)}\n")
            sys.exit(2)
        # Light type coercion. Numbers and booleans get parsed; strings stay
        # strings.
        if val.lower() in ("true", "false"):
            body[key] = val.lower() == "true"
        else:
            try:
                body[key] = int(val)
            except ValueError:
                try:
                    body[key] = float(val)
                except ValueError:
                    body[key] = val
    return body


def cmd_list(args, client: VoogClient) -> int:
    rows = client.get_all("/discounts", base=client.ecommerce_url)
    print(f"{'ID':<8} {'Code':<22} {'Status':<10} {'Type':<12} Amount")
    print("-" * 70)
    for r in rows:
        print(
            f"{r.get('id', ''):<8} {r.get('code', '')!s:<22} "
            f"{r.get('status', '')!s:<10} {r.get('discount_type', '')!s:<12} "
            f"{r.get('amount', '')}"
        )
    print(f"\nTotal: {len(rows)} discounts")
    return 0


def cmd_get(args, client: VoogClient) -> int:
    row = client.get(f"/discounts/{args.discount_id}", base=client.ecommerce_url)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_create(args, client: VoogClient) -> int:
    body = _parse_kv_pairs(args.fields)
    body["code"] = args.code
    result = client.post("/discounts", {"discount": body}, base=client.ecommerce_url)
    print(f"  created discount {result.get('id')}: {result.get('code')!r}")
    return 0


def cmd_update(args, client: VoogClient) -> int:
    body = _parse_kv_pairs(args.fields)
    if not body:
        sys.stderr.write("error: supply at least one key=value pair\n")
        return 2
    result = client.put(
        f"/discounts/{args.discount_id}",
        {"discount": body},
        base=client.ecommerce_url,
    )
    print(f"  updated discount {result.get('id')}: {sorted(body.keys())}")
    return 0


def cmd_delete(args, client: VoogClient) -> int:
    if not args.force:
        sys.stderr.write(f"error: refusing to delete discount {args.discount_id} without --force\n")
        return 2
    client.delete(f"/discounts/{args.discount_id}", base=client.ecommerce_url)
    print(f"  deleted discount {args.discount_id}")
    return 0
