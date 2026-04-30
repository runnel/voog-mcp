# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Performance
- `voog site-snapshot` now fetches per-page contents, per-article details, and per-product details in parallel (max 8 workers each), matching the MCP `_site_snapshot` pattern. The CLI was the last sequential outlier. Closes #85.

### Changed
- `parallel_map` docstring documents the single-item synchronous path's behavior delta (runs ``fn`` on the calling thread, not a worker thread). Closes #85.
- `test_pages_snapshot_uses_parallel_map` (CLI + MCP) now also asserts ``max_workers=8`` and that the captured fetch fn targets ``/pages/{pid}/contents``. Closes #85.

## [1.1.1] — 2026-04-30

### Changed
- `voog pages-pull` now uses the shared `simplify_pages` projection helper instead of its own inline copy. No behavior change. Closes #73.
- Internal: redirect API payload now built via shared `voog._payloads.build_redirect_payload` helper, used by both CLI and MCP. Reduces drift risk if Voog changes the schema. Closes #75.
- `parallel_map` now executes single-item lists synchronously, skipping the ThreadPoolExecutor overhead (~10-50ms savings per single-item call). Output shape unchanged. Closes #76.

### Performance
- `voog pages-snapshot` now fetches per-page contents in parallel (max 8 workers), ~5-10x faster on sites with 50+ pages. Per-page error handling preserved. Closes #74.

## [1.1.0] — 2026-04-30

### Added
- `voog.json` site entries now accept `api_key` inline (in addition to the existing `api_key_env` env-var-name reference). Closes #70.

### Changed
- Per-repo site selection now uses `voog.json` (same schema as the home-level config) instead of the bespoke `voog-site.json` format. Drop a minimal `{"default_site": "<name>"}` at the repo root. Closes #71.
- `voog config init` now `chmod 0600`s the generated file and prints a stderr note that it contains a plaintext API token, with a pointer at the `api_key_env` alternative for shared/CI configs.
- `load_global_config` rejects empty / whitespace-only `api_key` and `api_key_env` values with a clear error instead of silently passing them through to a confusing 401 at API-call time.

### Deprecated
- `voog-site.json` still works with a deprecation warning. Migration: rename to `voog.json` and use `default_site` instead of `site`. Earliest removal: v2.0.

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

[1.1.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.1
[1.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.0
[1.0.2]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.2
[1.0.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.1
[1.0.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.0
[0.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v0.1.0
