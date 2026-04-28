"""Multi-site configuration loading and site resolution.

Two files participate:

1. Global XDG config (``${XDG_CONFIG_HOME:-~/.config}/voog/voog.json``):
   the source-of-truth registry of sites. Format:

       {
         "sites": {
           "<name>": {"host": "<domain>", "api_key_env": "<ENV_VAR_NAME>"}
         },
         "default_site": "<name|null>",
         "env_file": "<path|null>"
       }

2. Repo-local pointer (``voog-site.json`` in cwd or any parent up to 6
   levels): selects a site by name from the global registry. Format:

       {"site": "<name>"}

   The legacy format ``{"host": "...", "api_key_env": "..."}`` is still
   read by the CLI (with a deprecation warning) — but cannot be used
   alone, since it does not name a registered site. Callers handle this
   case explicitly via ``RepoSitePointer.legacy_*`` fields.

CLI site resolution order: ``--site`` flag → repo pointer → default_site
→ raise ConfigError. The MCP server does not call ``resolve_site``: it
takes ``site=`` explicitly on every tool call.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

REPO_POINTER_FILENAME = "voog-site.json"
MAX_PARENT_LEVELS = 6


class ConfigError(RuntimeError):
    """Generic configuration error (malformed file, no site resolved, etc.)."""


class UnknownSiteError(ConfigError):
    """Caller asked for a site not present in the global config."""


@dataclass(frozen=True)
class SiteConfig:
    name: str
    host: str
    api_key_env: str


@dataclass(frozen=True)
class GlobalConfig:
    sites: dict[str, SiteConfig] = field(default_factory=dict)
    default_site: str | None = None
    env_file: str | None = None


@dataclass(frozen=True)
class RepoSitePointer:
    """Result of finding a voog-site.json. Either site_name is set
    (modern format) or legacy_host + legacy_api_key_env are set."""

    site_name: str | None = None
    legacy_host: str | None = None
    legacy_api_key_env: str | None = None
    path: Path | None = None


def default_global_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "voog" / "voog.json"


def load_global_config(path: Path | None = None) -> GlobalConfig:
    """Read the XDG global config. Missing file returns empty config."""
    cfg_path = path if path is not None else default_global_config_path()
    if not cfg_path.exists():
        return GlobalConfig()
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"malformed config at {cfg_path}: {exc}") from exc

    sites_raw = raw.get("sites", {}) or {}
    sites: dict[str, SiteConfig] = {}
    for name, entry in sites_raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"site '{name}' must be an object")
        host = entry.get("host")
        api_key_env = entry.get("api_key_env")
        if not host or not api_key_env:
            raise ConfigError(f"site '{name}' must have both 'host' and 'api_key_env' fields")
        sites[name] = SiteConfig(name=name, host=host, api_key_env=api_key_env)

    default_site = raw.get("default_site")
    if default_site is not None and default_site not in sites:
        raise ConfigError(f"default_site '{default_site}' is not in sites: {sorted(sites)}")

    env_file = raw.get("env_file")
    return GlobalConfig(sites=sites, default_site=default_site, env_file=env_file)


def find_repo_site_pointer(cwd: Path) -> RepoSitePointer | None:
    """Walk up from ``cwd`` looking for ``voog-site.json``. Returns None if
    not found within MAX_PARENT_LEVELS. Emits a deprecation warning when
    the legacy format ``{host, api_key_env}`` is encountered."""
    cur = cwd.resolve()
    for _ in range(MAX_PARENT_LEVELS + 1):
        candidate = cur / REPO_POINTER_FILENAME
        if candidate.exists():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"malformed {candidate}: {exc}") from exc
            if isinstance(raw, dict) and "site" in raw:
                return RepoSitePointer(site_name=raw["site"], path=candidate)
            if isinstance(raw, dict) and "host" in raw and "api_key_env" in raw:
                warnings.warn(
                    f"{candidate} uses deprecated format. Replace with "
                    f'{{"site": "<name>"}} and add the site to the global '
                    f"config at {default_global_config_path()}.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return RepoSitePointer(
                    legacy_host=raw["host"],
                    legacy_api_key_env=raw["api_key_env"],
                    path=candidate,
                )
            raise ConfigError(
                f'{candidate} must contain either {{"site": "<name>"}} '
                "or the legacy {host, api_key_env} format"
            )
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def resolve_site(
    global_cfg: GlobalConfig,
    flag_site: str | None,
    cwd: Path,
) -> SiteConfig:
    """Resolve which site to use, following CLI priority.

    Order: ``flag_site`` → repo pointer (modern format only) → default_site.
    Raises UnknownSiteError if a name is given but not in the registry.
    Raises ConfigError if no site can be determined.

    Legacy-format repo pointers are *not* used here: callers (the CLI) must
    detect them via ``find_repo_site_pointer`` and handle migration explicitly.
    """
    if flag_site is not None:
        if flag_site not in global_cfg.sites:
            raise UnknownSiteError(
                f"unknown site '{flag_site}'. Available: {sorted(global_cfg.sites)}"
            )
        return global_cfg.sites[flag_site]

    pointer = find_repo_site_pointer(cwd)
    if pointer is not None and pointer.site_name is not None:
        if pointer.site_name not in global_cfg.sites:
            raise UnknownSiteError(
                f"voog-site.json points to '{pointer.site_name}' but that site "
                f"is not in the global config. Available: {sorted(global_cfg.sites)}"
            )
        return global_cfg.sites[pointer.site_name]

    if global_cfg.default_site is not None:
        return global_cfg.sites[global_cfg.default_site]

    raise ConfigError(
        "no site specified. Pass --site <name>, create voog-site.json in this "
        "tree, or set default_site in the global config. "
        f"Available: {sorted(global_cfg.sites) or '(none configured)'}"
    )


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=value .env file. Missing file returns ``{}``.

    Skips blank lines and ``#``-comments. Does NOT expand shell variables.
    """
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def find_env_file(global_cfg: GlobalConfig, cwd: Path) -> Path | None:
    """Locate a .env file. Priority: global_cfg.env_file → cwd .env →
    parents (up to MAX_PARENT_LEVELS) → ~/.config/voog/.env. Returns the
    first existing path or None."""
    candidates: list[Path] = []
    if global_cfg.env_file:
        candidates.append(Path(global_cfg.env_file).expanduser())
    cur = cwd.resolve()
    for _ in range(MAX_PARENT_LEVELS + 1):
        candidates.append(cur / ".env")
        if cur.parent == cur:
            break
        cur = cur.parent
    candidates.append(default_global_config_path().parent / ".env")
    for c in candidates:
        if c.exists():
            return c
    return None
