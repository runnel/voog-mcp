"""voog categories — manage ecommerce categories."""

from __future__ import annotations

import json
import sys

from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser("categories", help="List all categories")
    list_p.set_defaults(func=cmd_list)

    get_p = subparsers.add_parser("category", help="Get a category by id")
    get_p.add_argument("category_id", type=int)
    get_p.set_defaults(func=cmd_get)

    create_p = subparsers.add_parser("category-create", help="Create a category")
    create_p.add_argument("name")
    create_p.add_argument("--slug")
    create_p.add_argument("--parent-id", type=int, dest="parent_id")
    create_p.set_defaults(func=cmd_create)

    update_p = subparsers.add_parser("category-update", help="Update a category")
    update_p.add_argument("category_id", type=int)
    update_p.add_argument("--name")
    update_p.add_argument("--slug")
    update_p.add_argument("--parent-id", type=int, dest="parent_id")
    update_p.set_defaults(func=cmd_update)

    del_p = subparsers.add_parser("category-delete", help="Delete a category (requires --force)")
    del_p.add_argument("category_id", type=int)
    del_p.add_argument("--force", action="store_true", help="Required — destructive")
    del_p.set_defaults(func=cmd_delete)


def cmd_list(args, client: VoogClient) -> int:
    cats = client.get_all("/categories", base=client.ecommerce_url)
    print(f"{'ID':<8} {'Depth':<6} {'Parent':<8} Slug / Name")
    print("-" * 80)
    for c in cats:
        cid = str(c.get("id", ""))
        depth = str(c.get("depth", "?"))
        parent = str(c.get("parent_id") or "")
        slug = c.get("slug", "")
        name = c.get("name", "")
        print(f"{cid:<8} {depth:<6} {parent:<8} {slug:<35} {name}")
    print(f"\nTotal: {len(cats)} categories")
    return 0


def cmd_get(args, client: VoogClient) -> int:
    cat = client.get(f"/categories/{args.category_id}", base=client.ecommerce_url)
    print(json.dumps(cat, indent=2, ensure_ascii=False))
    return 0


def cmd_create(args, client: VoogClient) -> int:
    body: dict = {"name": args.name}
    if args.slug is not None:
        body["slug"] = args.slug
    if args.parent_id is not None:
        body["parent_id"] = args.parent_id
    result = client.post("/categories", {"category": body}, base=client.ecommerce_url)
    print(f"  created category {result.get('id')}: {result.get('name')!r}")
    return 0


def cmd_update(args, client: VoogClient) -> int:
    body: dict = {}
    if args.name is not None:
        body["name"] = args.name
    if args.slug is not None:
        body["slug"] = args.slug
    if args.parent_id is not None:
        body["parent_id"] = args.parent_id
    if not body:
        sys.stderr.write("error: supply at least one of --name / --slug / --parent-id\n")
        return 2
    result = client.put(
        f"/categories/{args.category_id}",
        {"category": body},
        base=client.ecommerce_url,
    )
    print(f"  updated category {result.get('id')}: {sorted(body.keys())}")
    return 0


def cmd_delete(args, client: VoogClient) -> int:
    if not args.force:
        sys.stderr.write(f"error: refusing to delete category {args.category_id} without --force\n")
        return 2
    client.delete(f"/categories/{args.category_id}", base=client.ecommerce_url)
    print(f"  deleted category {args.category_id}")
    return 0
