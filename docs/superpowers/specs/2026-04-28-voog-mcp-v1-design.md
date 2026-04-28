# voog-mcp v1.0 — Design

**Date:** 2026-04-28
**Status:** Approved (brainstorming phase complete; ready for implementation plan)

## Background

There are currently two co-existing tools for working with the Voog CMS API:

1. **`voog.py`** — a 1719-line CLI script (`Tööriistad/voog.py`) that authenticates from `voog-site.json` in the current working directory and executes commands against the Voog API (pull/push templates, products, pages, layouts, redirects, snapshots, local proxy).
2. **`voog_mcp/`** — a Python MCP server (`Tööriistad/voog_mcp/`) exposing a subset of the same operations as MCP tools and resources, authenticated via `VOOG_HOST` + `VOOG_API_TOKEN` env vars.

`voog.py` already imports `VoogClient` from `voog_mcp` (`voog.py:90`), so the two are de-facto a single project that has not yet been packaged as one.

Both are **personal/Estonian-specific** in their current form:
- `voog.py` help text, comments, error messages, and docstrings are in Estonian
- `voog.py` `LOCAL_ASSETS` dict (lines 1312–1336) hardcodes Stella's specific filenames (`cart.js`, `stella-id.js`, `newsletter-drawer.js`, etc.)
- `voog.py` references "Stella vs Runnel", `Claude/.env`, `stellasoomlais-voog/` in error messages and docstrings
- `voog_mcp/` has 7 Estonian error strings (mostly in `config.py`)

## Goals

- Publish as an open-source project: `runnel/voog-mcp` on GitHub, eventually on PyPI as `voog-mcp`
- Single Python package, two entry points: `voog` (CLI) + `voog-mcp` (MCP server)
- First-class multi-site support: a single CLI/MCP install can manage any number of Voog sites
- Maintain the existing "wrong-site safety" pattern (currently enforced via `voog-site.json`)
- Eliminate all personal/site-specific references; English only

## Non-goals

- No backwards compatibility with the personal `voog.py` outside of a one-shot migration of the deprecated `voog-site.json` format
- No PyPI publish at v0.x; v1.0 release tag triggers PyPI publish
- No multi-language UI; English only
- No webhook server, no Voog OAuth, no Liquid linter — these are out of scope for v1.0

## Architecture decisions

### D1. One package, two entry points (vs MCP-only or two packages)

Single Python package `voog-mcp` (PyPI name) with import name `voog`. Exposes:
- `voog` console script — CLI
- `voog-mcp` console script — MCP server

Shared library code (`voog/api/`, `voog/client.py`, `voog/config.py`) is consumed by both.

**Rejected:** MCP-only (leaves shell users without a tool); two separate packages (release-cycle overhead disproportionate to project size).

### D2. Multi-site: explicit `site` parameter on every MCP tool

Every MCP tool requires a `site: str` first parameter, with one exception: `voog_list_sites` (the discovery tool) takes no parameters and returns the list of configured sites. No "active site" state inside the MCP server.

**Rejected:** stateful "active site" with `set_site` tool (risk of writes to wrong site after long sessions); MCP reading cwd from Claude's `roots` (fragile, not all clients pass cwd).

### D3. Two-file config with priority

- **Global XDG config** (`${XDG_CONFIG_HOME:-~/.config}/voog/voog.json`) — site registry, optional `default_site`, optional `env_file` path
- **Repo-local file** (`voog-site.json`) — pointer to a site by name; preserves the existing "wrong-site safety" pattern for the CLI

CLI site resolution order: `--site` flag → `voog-site.json` (cwd ↑6 parents) → `default_site` → error.
MCP does **not** read `voog-site.json` or `default_site`; the `site` parameter is always explicit.

**Rejected:** XDG-only (loses safety pattern); cwd-only (MCP cannot start without a site context).

### D4. Distribution: GitHub during v0.x, PyPI from v1.0

During v0.x development, users install via `uvx --from git+https://github.com/runnel/voog-mcp.git voog-mcp`. From v1.0, PyPI publish on tag triggers `uvx voog-mcp` to work directly.

### D5. Naming

- PyPI / GitHub repo: `voog-mcp`
- Import name: `voog`
- CLI binary: `voog`
- MCP binary: `voog-mcp`
- License: MIT
- Default branch: `main`

## Repo structure

```
voog-mcp/                          # GitHub: runnel/voog-mcp
├── pyproject.toml                 # name=voog-mcp, scripts: voog + voog-mcp
├── README.md                      # English: install, configure, MCP setup
├── LICENSE                        # MIT
├── CHANGELOG.md
├── .github/workflows/
│   ├── test.yml                   # ruff + pytest, Python 3.10/3.11/3.12
│   └── publish.yml                # PyPI publish on tag (v1.0+)
├── src/voog/
│   ├── __init__.py
│   ├── __main__.py                # python -m voog → CLI
│   ├── client.py                  # VoogClient (HTTP, urllib-based)
│   ├── config.py                  # SiteConfig, load_global_config, resolve_site
│   ├── errors.py
│   ├── _concurrency.py
│   ├── projections.py
│   ├── api/                       # business logic shared between CLI & MCP
│   │   ├── layouts.py
│   │   ├── pages.py
│   │   ├── products.py
│   │   ├── redirects.py
│   │   ├── snapshot.py
│   │   └── serve.py               # local proxy (was voog.py serve)
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py                # argparse dispatch
│   │   └── commands/              # one file per command group
│   │       ├── config.py          # config init / list-sites / check
│   │       ├── pull.py
│   │       ├── push.py
│   │       ├── list.py
│   │       ├── serve.py
│   │       ├── products.py
│   │       ├── pages.py
│   │       ├── layouts.py
│   │       └── redirects.py
│   └── mcp/
│       ├── __init__.py
│       ├── server.py
│       ├── tools/
│       │   ├── layouts.py
│       │   ├── pages.py
│       │   ├── products.py
│       │   ├── redirects.py
│       │   └── snapshot.py
│       └── resources/
│           ├── articles.py
│           ├── layouts.py
│           ├── pages.py
│           ├── products.py
│           └── redirects.py
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_client.py
    ├── test_cli/
    │   ├── test_main.py
    │   ├── test_pull_push.py
    │   ├── test_products.py
    │   ├── test_pages.py
    │   ├── test_serve.py
    │   └── test_layouts.py
    └── test_mcp/
        ├── test_server.py
        ├── test_site_param.py
        ├── test_tools_layouts.py
        ├── test_tools_pages.py
        ├── test_tools_products.py
        └── test_resources.py
```

`pyproject.toml` core:
```toml
[project]
name = "voog-mcp"
version = "0.1.0"
description = "CLI and MCP server for Voog CMS — Liquid templates, pages, products, ecommerce"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
dependencies = ["mcp>=0.9.0"]

[project.optional-dependencies]
dev = ["ruff", "pytest"]

[project.scripts]
voog = "voog.cli.main:main"
voog-mcp = "voog.mcp.server:main"

[tool.setuptools.packages.find]
where = ["src"]
```

## Multi-site config

### Global config: `~/.config/voog/voog.json`

```json
{
  "sites": {
    "stella":  {"host": "stellasoomlais.com", "api_key_env": "VOOG_API_KEY"},
    "runnel":  {"host": "runnel.ee",          "api_key_env": "RUNNEL_VOOG_API_KEY"}
  },
  "default_site": null,
  "env_file": "~/.config/voog/.env"
}
```

- Honors `XDG_CONFIG_HOME`; default `~/.config/voog/voog.json`
- `env_file` (optional) — explicit `.env` location
- `default_site` (optional) — used when no `--site` flag and no `voog-site.json` is found
- Env-search-order if `env_file` is unset: cwd `.env` → cwd parents (up to 6 levels) → `~/.config/voog/.env`

### Repo-local pointer: `voog-site.json`

```json
{"site": "stella"}
```

- Discovered by walking up from cwd (max 6 levels)
- Site name must exist in global config's `sites` map; otherwise error with available names

### Migration from deprecated format

Existing files in the form `{"host": "...", "api_key_env": "..."}` are still parsed by the CLI. They emit a deprecation warning suggesting the user replace contents with `{"site": "<name>"}` and add the site to the global config. This compatibility path will be removed in v2.0.

### MCP behavior

The MCP server reads only the global config. `voog-site.json` and `default_site` are ignored — every tool call must specify `site` explicitly. This is intentional: stateless tool calls are safer for long LLM sessions.

## CLI surface (`voog`)

Migrated from `argv`-parsing to `argparse`. All help text, errors, and prompts in English. Each command lives in its own `cli/commands/<name>.py` exporting `add_arguments(subparsers)` and `run(args, client)`.

```
voog [--site <name>] <command> [args...]

Site resolution:
  1. --site <name>             explicit
  2. voog-site.json (cwd ↑6)   repo-local
  3. default_site (XDG)        global default
  4. error: "no site specified"

Commands:
  config init                  interactively create XDG voog.json + .env
  config list-sites            show configured sites
  config check                 verify tokens (HEAD per site)

  pull                         download all template files into cwd
  push [<file>...]             upload file(s); no args = all (with confirmation)
  list                         list all files
  serve [--port 8080]          local proxy (auto-discovery: javascripts/, stylesheets/)

  products                     list all products
  product <id> [<field> <val>] info or update fields
  product-image <id> <file>... replace product images

  pages                        list all pages
  page <id>                    page detail
  page-create <title> <slug> <lang> [opts]
  page-set-hidden <id>... true|false
  page-set-layout <page-id> <layout-id>
  page-add-content <page_id> [name] [type]
  page-delete <id> [--force]
  pages-snapshot <dir>
  pages-pull

  site-snapshot <dir>          comprehensive read-only backup

  layout-rename <id> <new>
  layout-create [--content-type=…] <path>
  asset-replace <id> <new-filename>

  redirects
  redirect-add <from> <to> [301|302|307|410]
```

The `serve` command's hardcoded `LOCAL_ASSETS` dict is replaced with auto-discovery: scan `javascripts/*.js` and `stylesheets/*.css` in the local repo, build the substitution map at startup.

## MCP surface (`voog-mcp`)

All tools take `site: str` as the first parameter (D2), except `voog_list_sites` which is the discovery tool. All tool names are namespaced with `voog_`.

```
voog_list_sites()                                 → [{name, host}, ...]
voog_get_page(site, page_id)
voog_list_pages(site, limit?, offset?)
voog_get_layout(site, layout_id)
voog_list_layouts(site)
voog_update_layout(site, layout_id, body)
voog_get_product(site, product_id, include?)
voog_list_products(site, limit?, offset?)
voog_update_product(site, product_id, fields)
voog_replace_product_images(site, product_id, file_paths)
voog_list_redirects(site)
voog_create_redirect(site, source, target, status_code?)
voog_delete_redirect(site, redirect_id)
voog_site_snapshot(site, output_dir)
```

Resources are also namespaced: `voog://<site>/pages/<id>`, `voog://<site>/products/<id>`, etc. Reading from multiple sites in parallel works without state collisions.

### MCP server invocation

- Default: reads `${XDG_CONFIG_HOME:-~/.config}/voog/voog.json`
- `--config <path>` flag overrides
- `VOOG_CONFIG` env var also overrides

User-facing MCP setup (in README):

```json
{
  "mcpServers": {
    "voog": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/runnel/voog-mcp.git", "voog-mcp"]
    }
  }
}
```

After v1.0 PyPI release:

```json
{
  "mcpServers": {
    "voog": {"command": "uvx", "args": ["voog-mcp"]}
  }
}
```

## Error handling

All errors in English with actionable hints.

### Categories

1. **Config errors** — XDG file missing / malformed / site not listed:
   ```
   error: no site specified and no voog-site.json found in any parent dir.
   hint: run `voog config init` or pass `--site <name>`.
   sites: (none configured)
   ```

2. **Auth errors** — env var missing:
   ```
   error: env var 'VOOG_API_KEY' (referenced by site 'stella') is not set.
   hint: add it to ~/.config/voog/.env or your shell.
   ```

3. **API errors** — 401/403/404/422/5xx from Voog:
   - 401: hint "check your API token"
   - 422: print full response body (Voog returns structured field errors)
   - 5xx: retry once, then fail

4. **Tool input errors** in MCP — use the MCP framework's `isError: true` + textual content. Schema validation is delegated to the framework.

5. **Deprecated `voog-site.json` format**:
   ```
   warning: voog-site.json is using deprecated format. Migrating in-memory.
   hint: replace contents with `{"site": "<name>"}` and add the site to ~/.config/voog/voog.json.
   ```

### Logging and exit codes

- All logs/errors → stderr; stdout reserved for tool output
- MCP: `logger = logging.getLogger("voog")`
- CLI exit codes:
  - 0 = success
  - 1 = usage / config error
  - 2 = API error
- Never exit 0 on actual failure

## Testing strategy

TDD-friendly. HTTP-mocking via urllib monkey-patch (no real network calls in tests). Site resolution and config logic get the highest coverage because they enforce the safety contract.

### Critical test cases

**Site resolution** (highest priority — enforces wrong-site safety):
- `--site stella` → resolves
- `voog-site.json` in cwd → resolves
- `voog-site.json` in parent dir (up to 6 levels) → resolves
- `default_site` set, all else absent → resolves
- Ambiguous (both `voog-site.json` and `--site`) → flag wins
- Unknown site → error includes list of available sites
- Deprecated format `{host, api_key_env}` → migrates with warning
- MCP `voog_list_pages(site="nonexistent")` → tool-error, not 500

**Auth env loading**:
- `env_file` configured → loaded
- Default search (cwd → parents) → loaded
- Missing → clear error with hint

**CLI exit codes**:
- 0 = success only
- 1 = usage/config error
- 2 = API error

**MCP edge cases**:
- Tool call with missing `site` → schema validation error
- Tool call with `site=""` → explicit error
- Resource URI `voog://stella/pages/123` parses + resolves correctly

### CI

`.github/workflows/test.yml`:
- Matrix: Python 3.10, 3.11, 3.12
- Steps: `ruff check`, `ruff format --check`, `pytest`
- All green required pre-merge

### Release

`.github/workflows/publish.yml` triggers on `v*.*.*` tags from v1.0 onward, publishing to PyPI.

## voog.py audit findings (informing migration scope)

Findings from auditing the existing 1719-line `voog.py`:

**Hard blockers for public release:**
- Lines 1312–1336: `LOCAL_ASSETS` dict — hardcoded Stella filenames. Replace with fs-discovery in `api/serve.py`.
- Line 1080: hardcoded `$RUNNEL_VOOG_API_KEY` in error printout. Generalize.
- Line 1453: docstring mentions `stellasoomlais-voog/`. Generalize.

**Cosmetic but visible:**
- Help docstring (lines 1–78): all Estonian. Translate to English.
- ~183 lines have Estonian characters or common Estonian words (errors, prints, comments, docstrings). Translate.
- `Claude/.env` references in error messages — generalize to `~/.config/voog/.env` or just `.env`.
- "Stella vs Runnel" string in line 130 error message — replace with neutral example.

**Already clean:**
- `voog.py` already imports `VoogClient` from `voog_mcp` (line 90) — the two share an HTTP client.
- `voog-site.json` cwd-pattern is a reasonable safety idiom for public use; only error-text examples need generalization.
- `VoogClient`, `_concurrency.py`, `projections.py`, `errors.py` in `voog_mcp/` are essentially clean.

**Estimated rebrand scope:** ~250 lines of `voog.py` touched (mechanical translation + `LOCAL_ASSETS` auto-discovery + ~5 hardcoded examples). File size remains comparable.

## Implementation phases (high-level; detailed plan via writing-plans skill)

1. **Scaffold** — new `runnel/voog-mcp` repo, `src/`-layout, MIT license, README skeleton
2. **Move and rebrand** — copy `voog_mcp/` and `voog.py` into the new structure; translate to English; remove personal/Stella references
3. **Refactor `voog.py`** into `cli/commands/<name>.py` modules; switch to argparse
4. **Implement multi-site config** — `voog/config.py` with global XDG file + `voog-site.json` resolution; `voog config init|list-sites|check`
5. **Update MCP tools** to require `site` parameter; add `voog_list_sites`
6. **Tests** — site resolution, auth, CLI exit codes, MCP edge cases
7. **CI** — test.yml workflow
8. **README + CHANGELOG** — install via `uvx --from git+...`, config setup, MCP setup
9. **v0.1.0 tag** — initial public release on GitHub
10. **v1.0.0** later — once stable, add `publish.yml` and PyPI publish

## Open questions for the implementation plan

None. Brainstorming complete. Ready to hand off to the writing-plans skill.
