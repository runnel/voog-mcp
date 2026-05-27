"""voog tags — list / get / delete tags."""

from __future__ import annotations

import json
import sys

from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser("tags-list", help="List all tags")
    list_p.set_defaults(func=cmd_tags_list)

    get_p = subparsers.add_parser("tag-get", help="Get a single tag by id")
    get_p.add_argument("tag_id", type=int)
    get_p.set_defaults(func=cmd_tag_get)

    del_p = subparsers.add_parser("tag-delete", help="Delete a tag (irreversible)")
    del_p.add_argument("tag_id", type=int)
    del_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Skip confirmation prompt",
    )
    del_p.set_defaults(func=cmd_tag_delete)


def cmd_tags_list(args, client: VoogClient) -> int:
    try:
        tags = client.get_all("/tags")
    except Exception as e:
        sys.stderr.write(f"error: tags_list failed: {e}\n")
        return 1
    if not tags:
        print("No tags.")
        return 0
    print(f"{len(tags)} tag(s):")
    for t in sorted(tags, key=lambda x: (x.get("name") or "").lower()):
        tid = t.get("id", "?")
        name = (t.get("name") or "")[:30]
        count = t.get("taggings_count", "?")
        print(f"  id={tid:<6} {name:<30} taggings_count={count}")
    return 0


def cmd_tag_get(args, client: VoogClient) -> int:
    try:
        tag = client.get(f"/tags/{args.tag_id}")
    except Exception as e:
        sys.stderr.write(f"error: tag_get id={args.tag_id} failed: {e}\n")
        return 1
    print(json.dumps(tag, indent=2, ensure_ascii=False))
    return 0


def cmd_tag_delete(args, client: VoogClient) -> int:
    if not args.force:
        try:
            t = client.get(f"/tags/{args.tag_id}")
            print(
                f"  Deleting tag id={args.tag_id} name={t.get('name')!r} "
                f"taggings_count={t.get('taggings_count')}"
            )
        except Exception:
            print(f"  Deleting tag id={args.tag_id} (could not fetch tag info)")
        print("Confirm? (y/n) ", end="", flush=True)
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0
    try:
        client.delete(f"/tags/{args.tag_id}")
        print(f"  Deleted tag {args.tag_id}.")
    except Exception as e:
        sys.stderr.write(f"error: delete failed: {e}\n")
        return 1
    return 0
