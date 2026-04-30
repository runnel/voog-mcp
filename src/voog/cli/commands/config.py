"""voog config — manage global multi-site configuration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient
from voog.config import (
    ConfigError,
    default_global_config_path,
    find_env_file,
    load_env_file,
    load_global_config,
    resolve_site_token,
)


def add_arguments(subparsers):
    p = subparsers.add_parser("config", help="Manage global configuration")
    sub = p.add_subparsers(dest="config_action", required=True)

    init_p = sub.add_parser("init", help="Interactively create voog.json + .env")
    init_p.set_defaults(func=init)

    list_p = sub.add_parser("list-sites", help="List configured sites")
    list_p.set_defaults(func=list_sites)

    check_p = sub.add_parser("check", help="Verify all configured tokens (HEAD per site)")
    check_p.set_defaults(func=check)

    # Top-level dispatcher (sub_parsers required, so this is rarely hit)
    p.set_defaults(func=lambda args: 0)


def init(args) -> int:
    """Interactive: create XDG voog.json with tokens inline.

    Tokens go directly into voog.json by default. For shared or
    checked-in configs, edit afterwards to use ``api_key_env`` and
    keep secrets in ``.env`` or the environment.
    """
    cfg_path = args.config or default_global_config_path()
    cfg_path = Path(cfg_path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        sys.stderr.write(f"error: {cfg_path} already exists. Edit it directly.\n")
        return 1

    sites: dict = {}
    print(f"Creating {cfg_path}")
    while True:
        name = input("Site name (blank to finish): ").strip()
        if not name:
            break
        host = input(f"  Host for '{name}' (e.g., example.com): ").strip()
        token = input(f"  API token for '{name}' (paste from Voog Admin → API): ").strip()
        if not token:
            sys.stderr.write(f"error: api_key for '{name}' cannot be empty.\n")
            return 1
        sites[name] = {"host": host, "api_key": token}

    if not sites:
        sys.stderr.write("error: no sites entered. Aborting.\n")
        return 1

    default = None
    if len(sites) == 1:
        default = next(iter(sites))
    else:
        choice = input("Default site (blank for none): ").strip()
        if choice:
            if choice not in sites:
                sys.stderr.write(f"error: '{choice}' not in sites.\n")
                return 1
            default = choice

    body = json.dumps({"sites": sites, "default_site": default}, indent=2)
    cfg_path.write_text(f"{body}\n")
    print(f"\nWrote {cfg_path}")
    print(
        "\nNote: each site can also use 'api_key_env' (env var name) "
        "instead of inline 'api_key' — useful for shared/CI configs."
    )
    return 0


def _token_source_label(site) -> str:
    """Short label describing where the token comes from."""
    if site.api_key_env and site.api_key:
        return f"[{site.api_key_env} or inline]"
    if site.api_key_env:
        return f"[{site.api_key_env}]"
    return "[inline api_key]"


def list_sites(args) -> int:
    cfg = load_global_config(args.config)
    if not cfg.sites:
        print("(no sites configured — run `voog config init`)")
        return 0
    for name, site in cfg.sites.items():
        marker = " (default)" if cfg.default_site == name else ""
        print(f"  {name}: {site.host}  {_token_source_label(site)}{marker}")
    return 0


def check(args) -> int:
    """Verify each site by sending a HEAD to /admin/api/site."""
    cfg = load_global_config(args.config)
    if not cfg.sites:
        sys.stderr.write("error: no sites configured\n")
        return 1
    env_path = find_env_file(cfg, Path.cwd())
    env = load_env_file(env_path) if env_path else {}
    failures = 0
    for name, site in cfg.sites.items():
        try:
            token = resolve_site_token(site, env)
        except ConfigError as exc:
            print(f"  {name}: ✗ {exc}")
            failures += 1
            continue
        try:
            client = VoogClient(host=site.host, api_token=token)
            client.get("/site")
            print(f"  {name}: ✓ {site.host}")
        except Exception as exc:
            print(f"  {name}: ✗ {site.host} — {exc}")
            failures += 1
    return 1 if failures else 0
