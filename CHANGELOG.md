# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `products_list` (and the mirrored `voog://{site}/products` resource) now expose `stock`, `reserved_quantity`, `uses_variants`, `variants_count`, and `created_at` on every product in the simplified projection. Closes the inventory blind spot that previously forced a raw `curl` fallback to answer "what is the laoseis on these products" / "what was added this week" — `in_stock` was a boolean only, and `created_at` was missing entirely. Existing fields are unchanged; callers only see new keys appear. (#104)
- `product_get` (and `voog://{site}/products/{id}`) now request `?include=variants,variant_types,translations`, so the response includes the per-variant `variants[]` array with `stock`, `reserved_quantity`, `in_stock`, `variant_attributes_text`, and `variant_attributes`. Per-variant inventory is now visible via MCP without the raw API fallback. Verified empirically against a 9-variant Argilla tote; the response schema is otherwise unchanged. (#104)

## [1.2.2] — 2026-05-01

### Fixed
- `voog push` no longer false-positives `✗ stored size N does not match local M bytes` on assets containing non-ASCII content (em-dashes, ä/õ/ü, etc.). Voog's PUT response `size` field counts Unicode code points (Python `len(body)`), not UTF-8 bytes — empirically verified post-1.2.1 release across ASCII, em-dash, NFC `é`, and NFD `e + combining ́`. The 1.2.1 verification compared `size` against `len(body.encode("utf-8"))`, which silently passed ASCII-only files (where chars == bytes) but blocked any push containing multi-byte UTF-8 characters. Now compares against `len(body)` (char count) and the error message names "characters" not "bytes".

## [1.2.1] — 2026-05-01

### Fixed
- **`voog push` silently no-op'd on layout_assets in legacy manifests** — root cause of #96. Manifests written by the pre-rename `voog.py` script used `"type": "layout_asset"` for CSS/JS entries; current `voog pull` writes `"type": "asset"`. The 1.2.0 push dispatch only matched `"layout"` and `"asset"`, so legacy entries fell through both branches and the PUT was never sent — but `✓` was printed unconditionally. Now `"layout_asset"` is accepted as an alias for `"asset"`, so existing checkouts work without a forced re-pull. Closes #96.
- **`voog asset-replace` had the same bug pattern** — its manifest lookup `info.get("type") == "asset"` also fell through for legacy entries, leaving the local file/manifest update branch silently un-run after a successful API POST. Now matches both spellings.
- **Manifest self-heal:** on a successful push of a legacy `"layout_asset"` entry, the manifest writeback also normalizes the type field to `"asset"`, so checkouts gradually migrate without a forced re-pull.

### Changed
- `voog push` payload form aligned with `docs/voog-mcp-endpoint-coverage.md` and the MCP tool path: both `/layouts` and `/layout_assets` now send flat `{"body": …}` / `{"data": …}` instead of the wrapped `{"layout": …}` / `{"layout_asset": …}` form. Consistency-only — wrapped form is also accepted by Voog (verified empirically), so this is not the behaviour fix.
- `voog push` now verifies the PUT response before printing ✓. For assets it checks the response's `size` field against the local body's character count (corrected from "byte count" in 1.2.2 above); for layouts it parses both `updated_at` values (manifest's anchor and response) as ISO 8601 timestamps and confirms the response's value advanced. Mismatches print `✗ <path>: …` to stderr and the command exits non-zero. Both checks tolerate slim responses (signal missing → fall through) so they don't false-positive against older endpoints / older manifests.
- `voog push` writes the response's `updated_at` back into the manifest entry on success, so a second push without an intervening pull still has a fresh anchor for the layout verification check.

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

[1.2.2]: https://github.com/runnel/voog-mcp/releases/tag/v1.2.2
[1.2.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.2.1
[1.2.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.2.0
[1.1.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.1
[1.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.1.0
[1.0.2]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.2
[1.0.1]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.1
[1.0.0]: https://github.com/runnel/voog-mcp/releases/tag/v1.0.0
[0.1.0]: https://github.com/runnel/voog-mcp/releases/tag/v0.1.0
