"""voog element-move — reorder/reparent an element instance.

CLI parity for the MCP tool `element_move`. NOT for element_definitions
(schema), which remain passthrough.

Per Voog docs (https://www.voog.com/developers/api/resources/elements),
PUT /elements/{id}/move accepts QUERY-STRING params (not a JSON body):

  - `page_id` — new parent page id
  - `before`  — existing element id (place before it)
  - `after`   — existing element id (place after it)
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
        "--page-id",
        type=int,
        default=None,
        dest="page_id",
        help="New parent PAGE id (page.id from pages_list, not an element id)",
    )
    p.add_argument(
        "--before",
        type=int,
        default=None,
        help="Existing ELEMENT id; the moved element is placed before it",
    )
    p.add_argument(
        "--after",
        type=int,
        default=None,
        help="Existing ELEMENT id; the moved element is placed after it",
    )
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    params: dict = {}
    if args.page_id is not None:
        params["page_id"] = args.page_id
    if args.before is not None:
        params["before"] = args.before
    if args.after is not None:
        params["after"] = args.after
    if not params:
        sys.stderr.write("error: supply at least one of --page-id / --before / --after\n")
        return 1
    if "before" in params and "after" in params:
        sys.stderr.write("error: --before and --after are mutually exclusive — pick one\n")
        return 1
    try:
        client.put(f"/elements/{args.element_id}/move", params=params)
        print(f"  element {args.element_id} moved: {sorted(params.keys())}")
    except Exception as e:
        sys.stderr.write(f"error: element_move id={args.element_id} failed: {e}\n")
        return 1
    return 0
