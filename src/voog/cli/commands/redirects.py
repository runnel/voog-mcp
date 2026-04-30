"""voog redirects — manage 301/302/307/410 redirect rules."""

from __future__ import annotations

from voog._payloads import build_redirect_payload
from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser("redirects", help="List all redirect rules")
    list_p.set_defaults(func=cmd_list)

    add_p = subparsers.add_parser("redirect-add", help="Add a redirect rule")
    add_p.add_argument("source")
    add_p.add_argument("target")
    add_p.add_argument(
        "status_code",
        nargs="?",
        type=int,
        default=301,
        choices=[301, 302, 307, 410],
    )
    add_p.set_defaults(func=cmd_add)


def cmd_list(args, client: VoogClient) -> int:
    rules = client.get_all("/redirect_rules")
    if not rules:
        print("No redirect rules found.")
        return 0
    print(f"{'ID':<10} {'Type':<6} {'Source':<55} Target")
    print("-" * 110)
    for r in sorted(rules, key=lambda x: x.get("source", "")):
        rid = str(r.get("id", ""))
        rtype = str(r.get("redirect_type") or r.get("http_status_code") or "")
        src = r.get("source", "")
        dst = r.get("destination") or r.get("target", "")
        active = "" if r.get("active", True) else " [INACTIVE]"
        print(f"{rid:<10} {rtype:<6} {src:<55} {dst}{active}")
    print(f"\nTotal: {len(rules)} rules")
    return 0


def cmd_add(args, client: VoogClient) -> int:
    payload = build_redirect_payload(
        args.source,
        args.target,
        redirect_type=args.status_code,
    )
    rule = client.post("/redirect_rules", payload)
    print(
        f"  created redirect {rule.get('id')}: {args.source} -> {args.target} [{args.status_code}]"
    )
    return 0
