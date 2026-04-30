# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `voog.json` site entries now accept `api_key` inline (in addition to the existing `api_key_env` env-var-name reference). Closes #70.

## [1.0.2] — 2026-04-29

### Added
- `pip install voog-mcp` — first release distributed via PyPI (in addition to git+URL installs).
- `.github/workflows/publish.yml` — automated PyPI publish on `v*` tag push, using PyPI Trusted Publishers (OIDC, no API tokens).

## [1.0.1] — 2026-04-29

### Removed
- Stale smoke-test classes (`TestMCPSmokeTools`, `TestMCPSmokeResources`) and supporting infrastructure from `tests/test_mcp_integration.py`. They were gated behind `RUN_SMOKE=1` + `VOOG_SMOKE_HOST` and used pre-multi-site env-var auth + pre-namespace resource URIs that are incompatible with the v1.0 server. Closes #59.

## [1.0.0] — 2026-04-29

Stable release. API stabilized.

## [0.1.0] — 2026-04-28

Initial public release. Refactored from internal personal tooling.

### Added
- Single Python package `voog-mcp` with two entry points: `voog` (CLI) + `voog-mcp` (MCP server)
- Multi-site support via `~/.config/voog/voog.json`
- Per-repo site selection via `voog-site.json` (`{"site": "<name"}`)
- `voog config init / list-sites / check` for managing configuration
- `voog_list_sites` MCP tool for discovery
- All MCP tools require explicit `site` parameter
- MCP resources namespaced by site (`voog://<site>/...`)
- `serve` command auto-discovers local JS/CSS assets (no hardcoded list)
- CI on Python 3.10 / 3.11 / 3.12

### Changed
- `voog-site.json` legacy format `{host, api_key_env}` still parsed but deprecated
- All user-facing messages translated from Estonian to English

### Removed
- `voog.py` legacy script (replaced by `voog` CLI binary)
- `voog_mcp/` package layout (replaced by `src/voog/mcp/`)

[1.0.2]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.2
[1.0.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.1
[1.0.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.0
[0.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v0.1.0
