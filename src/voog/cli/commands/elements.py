"""voog element-move — reorder/reparent an element instance.

CLI parity for the MCP tool `element_move`. NOT for element_definitions
(schema), which remain passthrough.
"""

from __future__ import annotations

import sys

from voog.client import VoogClient


def add_arguments(subparsers):
    p = subparsers.add_parser(
        "element-move",
        help="Move (reorder/reparent) an element instance",
    )
    p.add_argument("element_id", type=int)
    p.add_argument(
        "--position",
        type=int,
        default=None,
        help="New 1-indexed position within parent",
    )
    p.add_argument(
        "--parent-id",
        type=int,
        default=None,
        dest="parent_id",
        help="New parent ELEMENT id (not page_id)",
    )
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    body: dict = {}
    if args.position is not None:
        body["position"] = args.position
    if args.parent_id is not None:
        body["parent_id"] = args.parent_id
    if not body:
        sys.stderr.write("error: supply at least one of --position / --parent-id\n")
        return 1
    try:
        client.put(f"/elements/{args.element_id}/move", body)
        print(f"  element {args.element_id} moved: {sorted(body.keys())}")
    except Exception as e:
        sys.stderr.write(f"error: element_move id={args.element_id} failed: {e}\n")
        return 1
    return 0
