"""voog articles comments — moderate article comments.

Mirrors the MCP tools `comments_list`, `comment_delete`,
`comment_toggle_spam`. Article CRUD itself lives in
`voog_admin_api_call` for now (articles_cmd not yet split out).
"""

from __future__ import annotations

import json
import sys

from voog.client import VoogClient


def add_arguments(subparsers):
    list_p = subparsers.add_parser(
        "comments-list",
        help="List comments on an article",
    )
    list_p.add_argument("article_id", type=int)
    list_p.set_defaults(func=cmd_comments_list)

    del_p = subparsers.add_parser(
        "comments-delete",
        help="Delete a comment (irreversible)",
    )
    del_p.add_argument("article_id", type=int)
    del_p.add_argument("comment_id", type=int)
    del_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Skip confirmation prompt",
    )
    del_p.set_defaults(func=cmd_comments_delete)

    spam_p = subparsers.add_parser(
        "comments-toggle-spam",
        help="Mark/unmark a comment as spam",
    )
    spam_p.add_argument("article_id", type=int)
    spam_p.add_argument("comment_id", type=int)
    spam_p.add_argument(
        "is_spam",
        choices=["true", "false"],
        help="true to mark spam, false to unmark",
    )
    spam_p.set_defaults(func=cmd_comments_toggle_spam)


def cmd_comments_list(args, client: VoogClient) -> int:
    try:
        comments = client.get_all(f"/articles/{args.article_id}/comments")
    except Exception as e:
        sys.stderr.write(f"error: comments_list article_id={args.article_id} failed: {e}\n")
        return 1
    if not comments:
        print(f"No comments on article {args.article_id}.")
        return 0
    print(f"{len(comments)} comment(s) on article {args.article_id}:")
    for c in comments:
        cid = c.get("id", "?")
        author = (c.get("author") or "?")[:20]
        is_spam = "[SPAM]" if c.get("is_spam") else "      "
        body = (c.get("body") or "").strip().replace("\n", " ")[:60]
        print(f"  {is_spam} id={cid:<6} {author:<20} {body}")
    print()
    print(json.dumps(comments, indent=2, ensure_ascii=False))
    return 0


def cmd_comments_delete(args, client: VoogClient) -> int:
    if not args.force:
        print(
            f"  Deleting comment {args.comment_id} on article {args.article_id}. Confirm? (y/n) ",
            end="",
            flush=True,
        )
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0
    try:
        client.delete(f"/articles/{args.article_id}/comments/{args.comment_id}")
        print(f"  Deleted comment {args.comment_id} on article {args.article_id}.")
    except Exception as e:
        sys.stderr.write(f"error: delete failed: {e}\n")
        return 1
    return 0


def cmd_comments_toggle_spam(args, client: VoogClient) -> int:
    is_spam = args.is_spam == "true"
    try:
        client.put(
            f"/articles/{args.article_id}/comments/{args.comment_id}",
            {"is_spam": is_spam},
        )
        verb = "marked spam" if is_spam else "unmarked spam"
        print(f"  Comment {args.comment_id} on article {args.article_id} {verb}.")
    except Exception as e:
        sys.stderr.write(f"error: toggle_spam failed: {e}\n")
        return 1
    return 0
