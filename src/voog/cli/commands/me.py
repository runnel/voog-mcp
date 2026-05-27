"""voog list-my-sites — probe /admin/api/me/sites.

CLI parity for the MCP tool `voog_list_my_sites`. Operator supplies
token+host on the command line. The CLI bootstraps a one-off
VoogClient (no `voog.json` site resolution) — useful BEFORE
`voog config init` to confirm the token works.
"""

from __future__ import annotations

import json
import os
import sys

from voog.client import VoogClient

_DEFAULT_HOST = "www.voog.com"


def add_arguments(subparsers):
    p = subparsers.add_parser(
        "list-my-sites",
        help="Probe /admin/api/me/sites from a token (token-scoped — returns 1 site)",
    )
    p.add_argument(
        "--token-env",
        default=None,
        dest="token_env",
        help="Env var name holding the Voog API token (preferred)",
    )
    p.add_argument(
        "--token",
        default=None,
        help="Raw Voog API token (fallback; appears in shell history)",
    )
    p.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help=f"Admin host (default: {_DEFAULT_HOST})",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    """Top-level run signature differs from other CLI commands —
    no client is passed because there's no site to resolve. main.py
    treats this command like `config` (no client construction).
    """
    if args.token_env and args.token:
        sys.stderr.write(
            "error: supply --token-env OR --token, not both "
            "(--token-env preferred — keeps secret out of shell history)\n"
        )
        return 1
    token: str | None = None
    if args.token_env:
        resolved = os.environ.get(args.token_env)
        # Distinguish unset from set-but-empty so operators with a
        # truncated `.env` paste get a useful error message.
        if resolved is None:
            sys.stderr.write(f"error: env var {args.token_env!r} is not set\n")
            return 1
        if not resolved.strip():
            sys.stderr.write(
                f"error: env var {args.token_env!r} is set but empty "
                "(check your .env or shell export)\n"
            )
            return 1
        token = resolved
    elif args.token:
        token = args.token
    else:
        sys.stderr.write("error: supply --token-env (preferred) or --token\n")
        return 1

    host = (args.host or _DEFAULT_HOST).strip()
    try:
        client = VoogClient(host=host, api_token=token)
        sites = client.get("/me/sites")
    except Exception as e:
        sys.stderr.write(f"error: list-my-sites failed: {e}\n")
        return 1
    if not isinstance(sites, list):
        sys.stderr.write(f"error: unexpected response shape ({type(sites).__name__})\n")
        return 1
    print(f"{len(sites)} site(s) reachable from this token:")
    for s in sites:
        name = s.get("name", "?")
        primary = s.get("primary_domain", "?")
        flags = s.get("feature_flags") or []
        flags_str = ",".join(flags) if isinstance(flags, list) else str(flags)
        print(f"  {name:<30} {primary:<40} flags=[{flags_str}]")
    if len(sites) == 1:
        print(
            "\nNote: Voog API tokens are site-scoped — this token reaches "
            "exactly one site. To enumerate more, supply each site's "
            "token separately."
        )
    # Also dump the raw JSON for piping into jq.
    print()
    print(json.dumps(sites, indent=2, ensure_ascii=False))
    return 0
