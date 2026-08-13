"""Voog CLI — argparse dispatch.

Each command lives in ``voog.cli.commands.<name>`` and exports:

    def add_arguments(subparsers): ...
    def run(args, client) -> int: ...

``main()`` resolves the site, instantiates the client, and dispatches.
Exit codes: 0 = success, 1 = config error, 2 = usage / API error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from voog.cli.commands import (
    articles as articles_cmd,
)
from voog.cli.commands import (
    cart_rules as cart_rules_cmd,
)
from voog.cli.commands import (
    categories as categories_cmd,
)
from voog.cli.commands import (
    clone as clone_cmd,
)
from voog.cli.commands import (
    config as config_cmd,
)
from voog.cli.commands import (
    discounts as discounts_cmd,
)
from voog.cli.commands import (
    elements as elements_cmd,
)
from voog.cli.commands import (
    layouts as layouts_cmd,
)
from voog.cli.commands import (
    list as list_cmd,
)
from voog.cli.commands import (
    me as me_cmd,
)
from voog.cli.commands import (
    orders as orders_cmd,
)
from voog.cli.commands import (
    pages as pages_cmd,
)
from voog.cli.commands import (
    products as products_cmd,
)
from voog.cli.commands import (
    pull as pull_cmd,
)
from voog.cli.commands import (
    push as push_cmd,
)
from voog.cli.commands import (
    redirects as redirects_cmd,
)
from voog.cli.commands import (
    search as search_cmd,
)
from voog.cli.commands import (
    serve as serve_cmd,
)
from voog.cli.commands import (
    shipping as shipping_cmd,
)
from voog.cli.commands import (
    snapshot as snapshot_cmd,
)
from voog.cli.commands import (
    tags as tags_cmd,
)
from voog.client import VoogClient
from voog.config import (
    ConfigError,
    UnknownSiteError,
    default_global_config_path,
    find_env_file,
    find_repo_site_pointer,
    load_env_file,
    load_global_config,
    load_merged_config,
    resolve_site,
    resolve_site_token,
)
from voog.logging import silence_transport_loggers

COMMANDS = [
    articles_cmd,
    config_cmd,
    elements_cmd,
    pull_cmd,
    push_cmd,
    list_cmd,
    serve_cmd,
    products_cmd,
    pages_cmd,
    layouts_cmd,
    me_cmd,
    redirects_cmd,
    search_cmd,
    clone_cmd,
    snapshot_cmd,
    tags_cmd,
    categories_cmd,
    orders_cmd,
    discounts_cmd,
    cart_rules_cmd,
    shipping_cmd,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voog",
        description="CLI for Voog CMS — Liquid templates, pages, products, ecommerce",
    )
    parser.add_argument(
        "--site",
        default=None,
        help="Site name from the global config. Overrides cwd-level voog.json and default_site.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Global config path (default: {default_global_config_path()})",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for module in COMMANDS:
        module.add_arguments(sub)
    return parser


def _build_client(args: argparse.Namespace) -> VoogClient:
    """Resolve site + load env, return a configured VoogClient."""
    config_path = args.config or (
        Path(os.environ["VOOG_CONFIG"]) if os.environ.get("VOOG_CONFIG") else None
    )
    cwd = Path.cwd()

    # Deprecation path: voog-site.json. The legacy {host, api_key_env}
    # form is wired directly to a VoogClient (it was never registered
    # in the global config). The modern {site} form acts as an
    # implicit --site override against the merged config.
    pointer = find_repo_site_pointer(cwd)
    if pointer and pointer.legacy_host:
        # Stay self-contained: this path predates cwd-level voog.json
        # and a malformed cwd voog.json above must not break it.
        home_only_cfg = load_global_config(config_path)
        env_path = find_env_file(home_only_cfg, cwd)
        env = load_env_file(env_path) if env_path else {}
        token = env.get(pointer.legacy_api_key_env) or os.environ.get(pointer.legacy_api_key_env)
        if not token:
            raise ConfigError(f"env var '{pointer.legacy_api_key_env}' is not set")
        return VoogClient(host=pointer.legacy_host, api_token=token)

    global_cfg = load_merged_config(cwd=cwd, home_path=config_path)
    flag_site = args.site
    pointer_supplied_flag = False
    if flag_site is None and pointer is not None and pointer.site_name is not None:
        flag_site = pointer.site_name
        pointer_supplied_flag = True

    try:
        site = resolve_site(global_cfg, flag_site=flag_site)
    except UnknownSiteError as exc:
        if pointer_supplied_flag:
            raise UnknownSiteError(
                f"{pointer.path} points to '{flag_site}' but that site is not "
                f"in the merged config. Available: {sorted(global_cfg.sites)}"
            ) from exc
        raise

    env_path = find_env_file(global_cfg, cwd)
    env = load_env_file(env_path) if env_path else {}
    try:
        token = resolve_site_token(site, env)
    except ConfigError as exc:
        if site.api_key_env and not site.api_key:
            hint = f"Hint: add {site.api_key_env}=<token> to "
            hint += str(env_path or default_global_config_path().parent / ".env")
            raise ConfigError(f"{exc}. {hint}") from exc
        raise
    # Mirror voog.mcp.server.ClientFactory.for_site: thread site_name +
    # daily_request_quota through so the per-site daily quota (Phase 6b
    # S-7) is honored on the CLI surface AND the snapshot manifest's
    # ``site`` field is populated. Without this the CLI undercounted
    # against the quota and CLI snapshots wrote ``site: ""`` in
    # _meta.json (caught in v1.4 phase 7 smoke test, 2026-05-28).
    return VoogClient(
        host=site.host,
        api_token=token,
        site_name=site.name,
        daily_request_quota=site.daily_request_quota,
    )


def main() -> None:
    # Clamp httpcore / hpack to WARNING before any client work — if the
    # operator's environment has root logging at DEBUG, those loggers
    # would otherwise dump the bearer token in HPACK frames. See
    # SECURITY.md → Logging.
    silence_transport_loggers()
    parser = build_parser()
    args = parser.parse_args()

    # `config` never needs a client (init is what creates one). Nor does
    # `list-my-sites` on its token+host path — that's the pre-`config init`
    # probe. With --site it resolves like every other command (#140 item 7).
    if args.command == "config" or (
        args.command == "list-my-sites" and not getattr(args, "site", None)
    ):
        # These bypass _build_client, so they need their own ConfigError
        # handling: a voog.json that fails validation (v1.5 host check, a
        # malformed entry) would otherwise print a raw traceback from the
        # two commands an operator runs precisely *because* the config is
        # broken.
        try:
            sys.exit(args.func(args))
        except ConfigError as exc:
            sys.stderr.write(f"error: {exc}\n")
            sys.exit(1)

    try:
        client = _build_client(args)
    except UnknownSiteError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.exit(1)
    except ConfigError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.exit(1)

    sys.exit(args.func(args, client))


if __name__ == "__main__":
    main()
