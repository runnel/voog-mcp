# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.1] — 2026-05-01

Post-1.2.0 hardening sweep — seven follow-up PRs addressing destructive PUT defaults, percent-encoding bypasses, atomicity gaps, and a long-standing `voog push` silent-no-op. No breaking changes for typed-tool callers; one CLI behaviour change documented under Changed.

### Security
- `_validate_data_key` and `_validate_path` (raw passthrough) now decode percent-encoding before all structural checks. Previously the `/?#` forbidden-char check ran on the raw key, but the `..` traversal check ran on the decoded form — `key="foo%2Fbar"` slipped past, and Apache normalises `%2F` to `/` server-side. `_validate_path` additionally loops `urllib.parse.unquote` until stable to defeat double-encoded `%252e%252e` traversal. Bounded at 8 iterations. (#92)
- `internal_*` prefix check on `*_set_data` / `*_delete_data` keys is now case-insensitive (was bypassable via `INTERNAL_x`). (#92)
- `voog_admin_api_call` / `voog_ecommerce_api_call` reject `path` containing `?` when `params=` is also non-empty (was producing malformed `/x?a=1?b=2` URLs). (#92)

### Fixed
- `product_update` PUT envelope now translates `asset_ids` → `assets:[{id:n}]` (POST-only field; on PUT, Voog silently kept only the first/hero image). `variants` without `variant_attributes` is rejected (or `force=true`) — Voog otherwise wipes ALL variants, even ones with `id`. `attributes` ∩ translation-source field overlap (across both explicit `translations` and the legacy `fields` shape) is detected and rejected — was producing undefined-behaviour envelopes. (#91)
- `product_set_images` had the same `asset_ids`-on-PUT silent-drop bug — fixed by mirroring the #91 envelope translation. Regression guard added. (#97)
- `page_update` rejects `parent_id == page_id` (self-parent cycle). (#93)
- `page_create` validates `content_type` against a known set instead of pass-through (was waiting for Voog 422 on typos). (#93)
- `site_update` extends `IMMUTABLE_SITE_FIELDS` with `id`, `created_at`, `updated_at` — round-tripping a GET back into a PUT no longer silently writes server-managed fields. (#93)
- `ecommerce_settings_update` validates `translations` inner shape per-language (dict + non-empty values), matching the `product_update` pattern — `translations={"products_url_slug": "products"}` (string instead of `{lang: value}`) now fails locally with a clear message instead of via a generic Voog 422. (#93)
- `article_publish` accepts optional `autosaved_title/body/excerpt` args. When all three are provided, the tool skips the GET and PUTs directly — single round-trip, no race window. The no-args branch keeps the GET+PUT fallback with the race window now documented honestly. (#95)
- `page_add_content` GETs `/contents` first by default and refuses if a content area with the same name already exists — was silently creating duplicates on repeat calls. `force=true` skips the pre-check for legitimate repeated-name templates. Pre-check uses `client.get_all` so paginated content lists are fully covered. (#95)
- `redirect_update` does GET-then-merge-then-PUT instead of partial body — Voog's PUT is full-replace and silently coerced unspecified fields like `active=False` back to defaults. (#95)
- `voog push` no longer silently no-ops on `layout_assets` (CSS/JS/images). The CLI was wrapping the PUT body in `{"layout_asset": {"data": …}}`, which Voog answers with 200 but does not persist; the documented flat `{"data": …}` form (already used by the MCP `layout_asset_update` tool) is required. Layouts switched to flat as well to match the documented convention. Push now surfaces a hard error when Voog echoes the resource back with the content field cleared, instead of printing ✓. Closes #96. (#98)
- `article_create` argument-presence checks use `is not None` instead of truthiness, matching `article_update`. Empty strings/lists are now legitimate "set this field empty" inputs in both directions. (#94)

### Changed
- `voog push` exit code is now `2` when at least one file fails the silent-no-op detector (was: always `0`). Other files in the same invocation still attempt their PUT. Shell scripts that depend on the always-`0` exit will need updating. (#98)
- `page_duplicate` summary now surfaces Voog's default `hidden=True` outcome — caller sees `"📑 page X duplicated → Y (hidden, use page_set_hidden(false) to publish)"`. (#94)
- `product_update` `force` schema property now declares `"default": False` for parity with `redirect_delete`. asset_ids inputs are validated up-front (list shape + integer-coercibility) with structured `error_response`. (#91)
- `VALID_PAGE_CONTENT_TYPES` now `frozenset` for parity with `VALID_STATUS`. (#93)
- VoogClient User-Agent bumped to `voog-mcp/1.2.1`.

### Refactored
- Extracted `_decode_until_stable(s, *, max_iter=8)` shared helper used by both `_validate_data_key` and `_validate_path`. The security-relevant 8-iteration bound now lives in one place. (#92)
- Extracted `validate_translations_shape(field, langs, *, tool_name)` shared helper now used by both `product_update` and `ecommerce_settings_update`. (#93)
- Extracted `REDIRECT_FIELDS` tuple at `redirects.py` module top, used by `_redirect_update`'s loop, error message, and merge envelope construction. A future Voog-side redirect field addition only needs touching one line. (#95)

### Docs
- `docs/voog-mcp-endpoint-coverage.md`: completed the matrix — added rows for `voog_list_sites`, `pages_snapshot`, `site_snapshot`, `layouts_pull`, `layouts_push`. Moved `text_get` from Write tools to Read tools (was miscategorised). All 51 tools now appear in the matrix exactly once. (#94)
- CHANGELOG migration example for `product_update` legacy `fields` → `translations` shape. (#94)

## [1.2.0] — 2026-05-01

### Added
- Generic Admin API + Ecommerce v1 passthrough tools `voog_admin_api_call` and `voog_ecommerce_api_call`. Forward any (method, path, body, params) through the configured VoogClient — closes the "fall back to curl" gap when no typed tool exists.
- Articles CRUD: `articles_list`, `article_get`, `article_create`, `article_update`, `article_publish`, `article_delete`. Captures the `autosaved_*` + `publishing:true` semantics from the project memory.
- Page mutations: `page_create`, `page_update`, `page_set_data`, `page_delete_data`, `page_duplicate`. `page_create` supports parallel-translation `node_id` parameter; `page_delete_data` requires `force=true`.
- Text content: `text_get`, `text_update`, `page_add_content`.
- Layout body update + asset CRUD: `layout_update`, `layout_delete`, `layout_asset_create`, `layout_asset_update`, `layout_asset_delete`. Pure-API editing without the layouts_pull/layouts_push filesystem detour.
- Multilingual helpers: `languages_list`, `nodes_list`, `node_get`. Enables the parallel-translation workflow without raw API calls.
- Redirect lifecycle: `redirect_update`, `redirect_delete`.
- Ecommerce settings: `ecommerce_settings_get`, `ecommerce_settings_update`. Per-language `products_url_slug` covered.
- Site singleton: `site_get`, `site_update`, `site_set_data`, `site_delete_data`. Refuses immutable `code` and protected `internal_*` keys client-side; `site_delete_data` requires `force=true`.
- `docs/voog-mcp-endpoint-coverage.md` — endpoint coverage reference doc.

### Changed
- `product_update` now accepts `attributes` (status, price, sale_price, sku, stock, description, category_ids, image_id, asset_ids, physical_properties, uses_variants, variant_types, variants) and `translations` (nested {field: {lang: value}}) in addition to the legacy `fields` shape. Validates `status` enum {`draft`, `live`}. Backwards-compatible.
- `simplify_languages` and `simplify_nodes` projection helpers added in `voog.projections`.
- VoogClient User-Agent bumped to `voog-mcp/1.2.0`.
- `parallel_map` docstring documents the single-item synchronous path's behavior delta (runs `fn` on the calling thread, not a worker thread). Closes #85.
- `test_pages_snapshot_uses_parallel_map` (CLI + MCP) now also asserts `max_workers=8` and that the captured fetch fn targets `/pages/{pid}/contents`. Closes #85.

### Performance
- `voog site-snapshot` now fetches per-page contents, per-article details, and per-product details in parallel (max 8 workers each), matching the MCP `_site_snapshot` pattern. The CLI was the last sequential outlier. Closes #85.

### Migration
- Existing `product_update` calls with `fields` keep working — the legacy shape is auto-routed into `translations`. Worked example:

  ```jsonc
  // Before (v1.1.x — still accepted, auto-translated):
  {
    "product_id": 42,
    "fields": { "name-et": "Punane kott", "description-et": "..." }
  }

  // After (v1.2.0 native shape — emit this directly when writing new code):
  {
    "product_id": 42,
    "translations": {
      "name":        { "et": "Punane kott" },
      "description": { "et": "..." }
    }
  }
  ```

- New tools are additive; no breaking changes to any v1.1.x tool.

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

[1.2.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.2.0
[1.1.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.1
[1.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.0
[1.0.2]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.2
[1.0.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.1
[1.0.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.0
[0.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v0.1.0
