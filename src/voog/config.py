"""Multi-site configuration loading and site resolution.

The same ``voog.json`` schema is recognized in two locations and merged:

1. Home / XDG config (``${XDG_CONFIG_HOME:-~/.config}/voog/voog.json``):
   the source-of-truth registry of sites. Format:

       {
         "sites": {
           "<name>": {"host": "<domain>", "api_key": "<token>"},
           "<other>": {"host": "<domain>", "api_key_env": "<ENV_VAR_NAME>"}
         },
         "default_site": "<name|null>",
         "env_file": "<path|null>"
       }

   Each site must have ``host`` and at least one of ``api_key`` (token
   inline) or ``api_key_env`` (env var name to resolve). When both are
   set, ``api_key_env`` wins if the env var is actually defined — this
   is the documented "shared/CI escape hatch" so a checked-in config
   can override an inline default with a per-environment secret.

2. Repo-local override (``voog.json`` in cwd or any parent up to 6
   levels, distinct from the home location). Same schema. Loaded by
   ``load_merged_config`` and deep-merged on top of the home config —
   per-site entries are added or overridden, ``default_site`` takes
   precedence over the home value. Drop a minimal
   ``{"default_site": "<name>"}`` to pin a repo to a specific site.

The legacy ``voog-site.json`` file is still parsed for backward
compatibility but emits a ``DeprecationWarning`` pointing the user to
the cwd-level ``voog.json`` form.

CLI site resolution order: ``--site`` flag → merged ``default_site``
→ raise ConfigError. The MCP server does not call ``resolve_site``: it
takes ``site=`` explicitly on every tool call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("voog.config")

REPO_POINTER_FILENAME = "voog-site.json"
CWD_CONFIG_FILENAME = "voog.json"
MAX_PARENT_LEVELS = 6


class ConfigError(RuntimeError):
    """Generic configuration error (malformed file, no site resolved, etc.)."""


class UnknownSiteError(ConfigError):
    """Caller asked for a site not present in the global config."""


@dataclass(frozen=True)
class SiteConfig:
    name: str
    host: str
    api_key_env: str | None = None
    api_key: str | None = None


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


_SITE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")


def _validate_site_name(name: str) -> None:
    """Reject site names that would break ``voog://{site}/...`` URI parsing.

    Allowed: alphanumeric, underscore, hyphen, dot. 1-64 chars.
    Reject: empty, whitespace, slashes, ``#``/``?``, unicode, very long names.
    """
    if not _SITE_NAME_RE.fullmatch(name):
        raise ConfigError(
            f"site name {name!r} is invalid — must match {_SITE_NAME_RE.pattern} "
            f"(letters/digits/_/-/. only, 1-64 chars). Site names are interpolated "
            f"into voog://{{site}}/... resource URIs and must be URL-safe."
        )


def load_global_config(
    path: Path | None = None,
    *,
    partial: bool = False,
) -> GlobalConfig:
    """Read a voog.json config file. Missing file returns empty config.

    When ``partial`` is True, the ``default_site`` value is loaded as-is
    without verifying that it appears in this file's ``sites`` map —
    used by ``load_merged_config`` for cwd-level overrides that may
    legitimately reference a site defined only in the home config.
    Direct callers (the home-config flow) leave ``partial=False`` to
    keep the strict check.
    """
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
        _validate_site_name(name)
        if not isinstance(entry, dict):
            raise ConfigError(f"site '{name}' must be an object")
        host = entry.get("host")
        api_key_env = entry.get("api_key_env")
        api_key = entry.get("api_key")
        if not host:
            raise ConfigError(f"site '{name}' must have a 'host' field")
        if api_key is not None and not str(api_key).strip():
            raise ConfigError(f"site '{name}' has an empty or whitespace-only 'api_key'")
        if api_key_env is not None and not str(api_key_env).strip():
            raise ConfigError(f"site '{name}' has an empty or whitespace-only 'api_key_env'")
        if not api_key and not api_key_env:
            raise ConfigError(
                f"site '{name}' must have either 'api_key' (inline token) "
                "or 'api_key_env' (env var name)"
            )
        sites[name] = SiteConfig(name=name, host=host, api_key_env=api_key_env, api_key=api_key)

    default_site = raw.get("default_site")
    if default_site is not None and not isinstance(default_site, str):
        raise ConfigError(
            f"default_site at {cfg_path} must be a string (got {type(default_site).__name__})"
        )
    if not partial and default_site is not None and default_site not in sites:
        raise ConfigError(f"default_site '{default_site}' is not in sites: {sorted(sites)}")

    env_file = raw.get("env_file")
    logger.info(
        "loaded config: %d site(s) configured, default_site=%r",
        len(sites),
        default_site,
    )
    return GlobalConfig(sites=sites, default_site=default_site, env_file=env_file)


def resolve_site_token(site: SiteConfig, env: dict[str, str]) -> str:
    """Resolve the API token for a site.

    Order: ``api_key_env`` (if set AND env var resolves to a non-blank
    value in ``env`` or ``os.environ``) → ``api_key`` (inline) →
    ConfigError. The env-var path wins when both are configured:
    this is the "shared/CI escape hatch" — a checked-in config can
    carry an inline default while a deployment overrides via
    environment. Blank env vars (empty string or whitespace-only) are
    treated as "unset" so we don't ship a bogus token to the API.
    """
    if site.api_key_env:
        raw_value = env.get(site.api_key_env) or os.environ.get(site.api_key_env) or ""
        token = raw_value.strip()
        if token:
            return token
    if site.api_key:
        if not site.api_key_env:
            logger.warning(
                "site %r uses inline api_key (no api_key_env) — env-var token resolution preferred for security",
                site.name,
            )
        return site.api_key
    if site.api_key_env:
        raise ConfigError(
            f"env var '{site.api_key_env}' (referenced by site '{site.name}') is not set "
            "and no inline 'api_key' fallback is configured"
        )
    raise ConfigError(f"site '{site.name}' has neither 'api_key' nor 'api_key_env' configured")


def find_cwd_config(cwd: Path, *, home_path: Path | None = None) -> Path | None:
    """Walk up from ``cwd`` looking for ``voog.json``. Returns the first
    match within ``MAX_PARENT_LEVELS`` parent directories. The home
    config path is excluded so that walking up through the home
    directory does not double-load it.
    """
    home_resolved = home_path.resolve() if home_path else None
    cur = cwd.resolve()
    for _ in range(MAX_PARENT_LEVELS + 1):
        candidate = cur / CWD_CONFIG_FILENAME
        if candidate.exists():
            if home_resolved is None or candidate.resolve() != home_resolved:
                return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def load_merged_config(
    cwd: Path | None = None,
    home_path: Path | None = None,
) -> GlobalConfig:
    """Load the home config and merge any cwd-level ``voog.json`` on top.

    Per-site entries from the cwd config are added to or overwrite the
    home config's ``sites`` map. ``default_site`` and ``env_file`` from
    the cwd config replace the home values when set. The home config
    location itself is excluded from the cwd walk so we do not load it
    twice when invoking from inside the home directory tree.
    """
    home_path = home_path if home_path is not None else default_global_config_path()
    home_cfg = load_global_config(home_path)
    cwd_resolved = (cwd if cwd is not None else Path.cwd()).resolve()
    cwd_path = find_cwd_config(cwd_resolved, home_path=home_path)
    if cwd_path is None:
        return home_cfg
    cwd_cfg = load_global_config(cwd_path, partial=True)

    merged_sites = dict(home_cfg.sites)
    merged_sites.update(cwd_cfg.sites)
    merged_default = cwd_cfg.default_site or home_cfg.default_site
    merged_env_file = cwd_cfg.env_file or home_cfg.env_file
    if merged_default is not None and merged_default not in merged_sites:
        raise ConfigError(
            f"default_site '{merged_default}' (from {cwd_path}) is not in the "
            f"merged site registry: {sorted(merged_sites) or '(none configured)'}"
        )
    return GlobalConfig(
        sites=merged_sites,
        default_site=merged_default,
        env_file=merged_env_file,
    )


def find_repo_site_pointer(cwd: Path) -> RepoSitePointer | None:
    """Walk up from ``cwd`` looking for ``voog-site.json``. Returns None
    if not found within MAX_PARENT_LEVELS.

    Deprecated since v1.1.0: ``voog-site.json`` is superseded by a
    cwd-level ``voog.json`` (see ``find_cwd_config`` /
    ``load_merged_config``). Both the modern ``{"site": "<name>"}``
    form and the legacy ``{"host", "api_key_env"}`` form keep working
    here, but each emits a ``DeprecationWarning`` pointing the user at
    the replacement.
    """
    cur = cwd.resolve()
    for _ in range(MAX_PARENT_LEVELS + 1):
        candidate = cur / REPO_POINTER_FILENAME
        if candidate.exists():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"malformed {candidate}: {exc}") from exc
            if isinstance(raw, dict) and "site" in raw:
                snippet = json.dumps({"default_site": raw["site"]})
                warnings.warn(
                    f"{candidate} is deprecated. Replace with a cwd-level "
                    f"voog.json containing {snippet} for the same effect.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return RepoSitePointer(site_name=raw["site"], path=candidate)
            if isinstance(raw, dict) and "host" in raw and "api_key_env" in raw:
                warnings.warn(
                    f"{candidate} uses deprecated format. Replace with a "
                    f"cwd-level voog.json containing the site definition "
                    f"and a default_site pointer.",
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
) -> SiteConfig:
    """Resolve which site to use against an already-merged config.

    Order: ``flag_site`` → ``global_cfg.default_site`` → ConfigError.
    Raises ``UnknownSiteError`` if a name is given but not in the
    registry. As of v1.1, callers must pass an already-merged config
    built via ``load_merged_config(cwd=...)`` if they want cwd-level
    overrides. The legacy ``voog-site.json`` lookup lives in the CLI's
    ``_build_client`` (deprecation path).
    """
    if flag_site is not None:
        if flag_site not in global_cfg.sites:
            raise UnknownSiteError(
                f"unknown site '{flag_site}'. Available: {sorted(global_cfg.sites)}"
            )
        return global_cfg.sites[flag_site]

    if global_cfg.default_site is not None:
        return global_cfg.sites[global_cfg.default_site]

    raise ConfigError(
        "no site specified. Pass --site <name>, drop a voog.json with "
        '{"default_site": "<name>"} in this tree, or set default_site in '
        "the home config. "
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
