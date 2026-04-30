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
    config as config_cmd,
)
from voog.cli.commands import (
    layouts as layouts_cmd,
)
from voog.cli.commands import (
    list as list_cmd,
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
    serve as serve_cmd,
)
from voog.cli.commands import (
    snapshot as snapshot_cmd,
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
    resolve_site,
    resolve_site_token,
)

COMMANDS = [
    config_cmd,
    pull_cmd,
    push_cmd,
    list_cmd,
    serve_cmd,
    products_cmd,
    pages_cmd,
    layouts_cmd,
    redirects_cmd,
    snapshot_cmd,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voog",
        description="CLI for Voog CMS — Liquid templates, pages, products, ecommerce",
    )
    parser.add_argument(
        "--site",
        default=None,
        help="Site name from the global config. Overrides voog-site.json and default_site.",
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
    global_cfg = load_global_config(config_path)

    cwd = Path.cwd()

    # Handle legacy voog-site.json (host+api_key_env directly)
    pointer = find_repo_site_pointer(cwd)
    if pointer and pointer.legacy_host:
        env_path = find_env_file(global_cfg, cwd)
        env = load_env_file(env_path) if env_path else {}
        token = env.get(pointer.legacy_api_key_env) or os.environ.get(pointer.legacy_api_key_env)
        if not token:
            raise ConfigError(f"env var '{pointer.legacy_api_key_env}' is not set")
        return VoogClient(host=pointer.legacy_host, api_token=token)

    site = resolve_site(global_cfg, flag_site=args.site, cwd=cwd)
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
    return VoogClient(host=site.host, api_token=token)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # `config` subcommands don't need a client (config init creates it).
    if args.command == "config":
        sys.exit(args.func(args))

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
