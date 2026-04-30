"""voog pages — list, get, create, modify, delete, and pull pages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient
from voog.projections import simplify_pages


def add_arguments(subparsers):
    # pages — list all
    list_p = subparsers.add_parser("pages", help="List all pages")
    list_p.set_defaults(func=cmd_pages)

    # page — get single page info
    get_p = subparsers.add_parser("page", help="Get a single page's info")
    get_p.add_argument("page_id")
    get_p.set_defaults(func=cmd_page)

    # page-create
    create_p = subparsers.add_parser(
        "page-create",
        help="Create a new page (POST /pages)",
    )
    create_p.add_argument("title")
    create_p.add_argument("slug")
    create_p.add_argument("language_id", type=int)
    create_p.add_argument(
        "--layout-id",
        type=int,
        default=None,
        dest="layout_id",
        metavar="N",
    )
    create_p.add_argument(
        "--parent-id",
        type=int,
        default=None,
        dest="parent_id",
        metavar="N",
    )
    create_p.add_argument(
        "--hidden",
        action="store_true",
        default=False,
    )
    create_p.set_defaults(func=cmd_page_create)

    # page-add-content
    add_content_p = subparsers.add_parser(
        "page-add-content",
        help="Add a content area + linked text to a page",
    )
    add_content_p.add_argument("page_id", type=int)
    add_content_p.add_argument(
        "name",
        nargs="?",
        default="body",
        help="Content name (default: body)",
    )
    add_content_p.add_argument(
        "content_type",
        nargs="?",
        default="text",
        help="Content type (default: text)",
    )
    add_content_p.set_defaults(func=cmd_page_add_content)

    # page-delete
    delete_p = subparsers.add_parser(
        "page-delete",
        help="Delete a page (irreversible)",
    )
    delete_p.add_argument("page_id")
    delete_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Skip confirmation prompt",
    )
    delete_p.set_defaults(func=cmd_page_delete)

    # page-set-hidden
    set_hidden_p = subparsers.add_parser(
        "page-set-hidden",
        help="Bulk toggle hidden flag on pages",
    )
    set_hidden_p.add_argument("page_ids", nargs="+", metavar="page_id")
    set_hidden_p.add_argument(
        "hidden",
        choices=["true", "false"],
        help="true or false",
    )
    set_hidden_p.set_defaults(func=cmd_page_set_hidden)

    # page-set-layout
    set_layout_p = subparsers.add_parser(
        "page-set-layout",
        help="Change a page's layout",
    )
    set_layout_p.add_argument("page_id")
    set_layout_p.add_argument("layout_id", type=int)
    set_layout_p.set_defaults(func=cmd_page_set_layout)

    # pages-pull
    pull_p = subparsers.add_parser(
        "pages-pull",
        help="Save simplified pages.json to current directory",
    )
    pull_p.set_defaults(func=cmd_pages_pull)


def cmd_pages(args, client: VoogClient) -> int:
    """List all pages: id, path, title, hidden, layout."""
    pages = client.get_all("/pages")
    print(f"{len(pages)} pages:")
    for p in sorted(pages, key=lambda x: x.get("path") or ""):
        pid = p.get("id")
        path = p.get("path") or "/"
        title = (p.get("title") or "").strip()[:40]
        hidden = "[hidden]" if p.get("hidden") else "        "
        layout_obj = p.get("layout") or {}
        layout = (
            p.get("layout_name")
            or p.get("layout_title")
            or (layout_obj.get("title") if isinstance(layout_obj, dict) else None)
            or "?"
        )
        print(f"  {hidden} {pid:>8} | /{path:<40} | {title:<40} | layout={layout}")
    return 0


def cmd_page(args, client: VoogClient) -> int:
    """Show full info for a single page."""
    p = client.get(f"/pages/{args.page_id}")
    print(f"Page id={p.get('id')}")
    print(f"  title       : {p.get('title')}")
    print(f"  path        : /{p.get('path') or ''}")
    print(f"  hidden      : {p.get('hidden')}")
    print(f"  layout_id   : {p.get('layout_id')}")
    layout = (
        p.get("layout_name") or p.get("layout_title") or (p.get("layout") or {}).get("title") or "?"
    )
    print(f"  layout_name : {layout}")
    print(f"  content_type: {p.get('content_type')}")
    lang = p.get("language") or {}
    print(f"  language    : {lang.get('code')} (id {lang.get('id')})")
    print(f"  parent_id   : {p.get('parent_id')}")
    print(f"  created_at  : {p.get('created_at')}")
    print(f"  updated_at  : {p.get('updated_at')}")
    print(f"  public_url  : {p.get('public_url')}")
    return 0


def cmd_page_create(args, client: VoogClient) -> int:
    """Create a new page (POST /pages).

    Notes:
    - parent_id is a page id, not a node_id. Root-level pages: omit parent_id.
    - Voog has no 'state' field on pages — use hidden=True for draft semantics.
    """
    payload: dict = {
        "title": str(args.title),
        "slug": str(args.slug),
        "language_id": int(args.language_id),
    }
    if args.layout_id is not None:
        payload["layout_id"] = int(args.layout_id)
    if args.parent_id is not None:
        payload["parent_id"] = int(args.parent_id)
    if args.hidden:
        payload["hidden"] = True

    print(f"POST /pages title={args.title!r} slug={args.slug!r} language_id={args.language_id}...")
    result = client.post("/pages", payload)
    new_id = result.get("id")
    if not new_id:
        sys.stderr.write(f"error: POST response missing id: {result!r}\n")
        return 1
    print(f"  Created page id={new_id} path=/{result.get('path', '')}")
    return 0


def cmd_page_add_content(args, client: VoogClient) -> int:
    """Create a content area + linked text for a page.

    Fresh pages return [] from GET /pages/{id}/contents until someone opens
    edit mode in the admin UI — this command shortcuts that.
    The name must match the layout's {% content %} tag:
      - unnamed {% content %} -> name="body" (default)
      - named {% content name="gallery_1" %} -> name="gallery_1"
    """
    page_id = args.page_id
    name = args.name
    content_type = args.content_type

    payload = {"name": str(name), "content_type": str(content_type)}
    print(f"POST /pages/{page_id}/contents name={name!r} content_type={content_type!r}...")
    result = client.post(f"/pages/{page_id}/contents", payload)
    content_id = result.get("id")
    text_id = (result.get("text") or {}).get("id")
    print(f"  Created content_id={content_id} text_id={text_id}")
    return 0


def cmd_page_delete(args, client: VoogClient) -> int:
    """Delete a page. Irreversible."""
    page_id = args.page_id
    force = args.force

    if not force:
        try:
            p = client.get(f"/pages/{page_id}")
            print(f"  Deleting: id={page_id} title={p.get('title')!r} path=/{p.get('path') or ''}")
        except Exception:
            print(f"  Deleting: id={page_id} (could not fetch page info)")
        print("Confirm? (y/n) ", end="", flush=True)
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client.delete(f"/pages/{page_id}")
        print(f"  Deleted page {page_id}")
    except Exception as e:
        sys.stderr.write(f"error: delete failed for page {page_id}: {e}\n")
        return 1
    return 0


def cmd_page_set_hidden(args, client: VoogClient) -> int:
    """Bulk toggle hidden flag. page_ids are all args except last; last is true/false."""
    # argparse puts all positional args into page_ids, including "true"/"false"
    # but we set choices=["true","false"] for the last arg... Actually with the
    # current subparser definition, page_ids captures all the page_id args and
    # hidden captures the true/false. Let's handle both the nargs="+" and the
    # explicit hidden choice correctly.
    page_ids = args.page_ids
    hidden = args.hidden == "true"

    flag = "hidden" if hidden else "visible"
    print(f"Setting {len(page_ids)} page(s) to: {flag}")
    fail_count = 0
    for pid in page_ids:
        try:
            client.put(f"/pages/{pid}", {"hidden": bool(hidden)})
            print(f"  {pid}")
        except Exception as e:
            sys.stderr.write(f"  error: {pid}: {e}\n")
            fail_count += 1

    if fail_count:
        print(f"  {fail_count}/{len(page_ids)} failed")
        return 1
    return 0


def cmd_page_set_layout(args, client: VoogClient) -> int:
    """Change a page's layout."""
    page_id = args.page_id
    layout_id = args.layout_id
    print(f"PUT /pages/{page_id} layout_id={layout_id}...")
    client.put(f"/pages/{page_id}", {"layout_id": layout_id})
    print(f"  page {page_id} -> layout {layout_id}")
    return 0


def cmd_pages_pull(args, client: VoogClient) -> int:
    """Save simplified pages.json to current directory."""
    local_dir = Path.cwd()
    pages = client.get_all("/pages")
    simplified = simplify_pages(pages)
    pages_path = local_dir / "pages.json"
    pages_path.write_text(json.dumps(simplified, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  pages.json saved ({len(simplified)} pages)")
    return 0
