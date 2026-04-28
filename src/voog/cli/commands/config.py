"""voog config — manage global multi-site configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from voog.client import VoogClient
from voog.config import (
    default_global_config_path,
    find_env_file,
    load_env_file,
    load_global_config,
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
    """Interactive: create XDG voog.json + (optional) .env."""
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
        env_var = input(f"  Env var that holds the API token for '{name}': ").strip()
        sites[name] = {"host": host, "api_key_env": env_var}

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

    cfg_path.write_text(json.dumps({"sites": sites, "default_site": default}, indent=2))
    print(f"\nWrote {cfg_path}")

    env_path = cfg_path.parent / ".env"
    if not env_path.exists():
        print(f"\nCreate {env_path} and add your API tokens. Example:")
        for site_name, entry in sites.items():
            print(f"  {entry['api_key_env']}=your_token_for_{site_name}")
    return 0


def list_sites(args) -> int:
    cfg = load_global_config(args.config)
    if not cfg.sites:
        print("(no sites configured — run `voog config init`)")
        return 0
    for name, site in cfg.sites.items():
        marker = " (default)" if cfg.default_site == name else ""
        print(f"  {name}: {site.host}  [{site.api_key_env}]{marker}")
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
        token = env.get(site.api_key_env) or os.environ.get(site.api_key_env)
        if not token:
            print(f"  {name}: ✗ token env '{site.api_key_env}' not set")
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
