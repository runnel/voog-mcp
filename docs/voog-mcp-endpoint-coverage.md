# Voog MCP — endpoint coverage reference

This document maps Voog API endpoints to MCP tools and notes envelope shapes,
mutable fields, and gotchas. Maintained alongside `src/voog/mcp/tools/` —
update this doc when a tool is added or a new endpoint quirk is discovered.

## API surfaces

- **Admin API** — `https://{host}/admin/api/*`
- **Ecommerce v1 API** — `https://{host}/admin/api/ecommerce/v1/*`
- Auth: `X-API-Token: <token>` header (already handled by `VoogClient`)
- Pagination default: 50 / max 250; `voog.client.VoogClient.get_all` defaults to 100 per page
- Filter syntax: `q.<obj>.<attr>.<comp>=value` (`$eq`, `$cont`, `$gt`, …)
- Response shaping: `include=foo,bar`, `language_code=<iso>`

## Coverage matrix

| Resource | Read tools | Write tools | Notes |
|---|---|---|---|
| Pages | `pages_list`, `page_get` | `page_set_hidden`, `page_set_layout`, `page_delete`, `page_create`, `page_update`, `page_set_data`, `page_delete_data`, `page_duplicate` | `parent_id` is a page id, NOT node_id; root pages omit `parent_id`. Parallel translations use `node_id` (see Multilingual). `page_delete_data` requires `force=true`. |
| Articles | `articles_list`, `article_get` | `article_create`, `article_update`, `article_publish`, `article_delete` | Use `autosaved_title/excerpt/body` on PUT; `publishing: true` to push autosaved → published. `description` ≠ `excerpt` (see skill memory). |
| Layouts | (resource only) | `layout_rename`, `layout_create`, `layout_update`, `layout_delete`, `asset_replace` | `PUT /layouts/{id}` accepts `body` + `title` only. |
| Layout assets | (resource only) | `layout_asset_create`, `layout_asset_update`, `layout_asset_delete` | PUT `data` only — `filename` is read-only (use `asset_replace`). |
| Texts | (none) | `text_get`, `text_update`, `page_add_content` | Page content bodies live here. Fresh pages return `[]` from `/contents` until edit-mode trigger. |
| Redirects | `redirects_list` | `redirect_add`, `redirect_update`, `redirect_delete` | redirect_type ∈ {301, 302, 307, 410}. |
| Languages | `languages_list` | (none) | Read-only here — language_id resolution helper for page_create. |
| Nodes | `nodes_list`, `node_get` | (none) | Helper for parallel translations: `POST /pages` with `node_id` of existing page. |
| Site | `site_get` | `site_update`, `site_set_data`, `site_delete_data` | `site.code` immutable once set. `data.internal_*` keys read-only. `site_delete_data` requires `force=true`. |
| Products | `products_list`, `product_get` | `product_update` (full fields), `product_set_images` | `description`, `status`, `price`, `sale_price`, `sku`, `stock`, `category_ids`, `physical_properties`, `variant_types`, `translations.*` all supported. PUT envelope is `{"product": {...}}`. |
| Ecommerce settings | `ecommerce_settings_get` | `ecommerce_settings_update` | Per-language `products_url_slug` lives in `translations`. |
| **Everything else** | `voog_admin_api_call(method, path, ...)` | `voog_ecommerce_api_call(method, path, ...)` | Generic passthrough — same auth, same timeout, no envelope assumed. Use for orders, carts, discounts, gateways, shipping_methods, forms, tickets, tags, elements, element_definitions, media_sets, webhooks, content_partials, templates, bulk update, imports, search. |

## Envelope conventions

Voog uses different wrapping conventions per endpoint. The `voog._payloads`
module centralises these so CLI and MCP cannot drift.

| Endpoint | Wrapper | Example |
|---|---|---|
| `POST/PUT /pages` | `{"page": {...}}` OR flat (Voog accepts both; flat is what the existing CLI uses) | `{"title": "...", "slug": "..."}` |
| `POST/PUT /articles` | flat | `{"autosaved_title": "...", "publishing": true}` |
| `POST/PUT /layouts` | flat | `{"title": "...", "body": "..."}` |
| `POST/PUT /layout_assets` | flat | `{"filename": "...", "asset_type": "...", "data": "..."}` |
| `PUT /texts/{id}` | flat | `{"body": "<html>..."}` |
| `POST /redirect_rules` | `{"redirect_rule": {...}}` | (already in `voog._payloads`) |
| `PUT /products/{id}` | `{"product": {...}}` | translations / fields nested |
| `PUT /products/{id}` for image_id+asset_ids | **flat, NOT wrapped** | `{"image_id": ..., "asset_ids": [...]}` (empirically confirmed in `product_set_images`) |
| `PUT /ecommerce/v1/settings` | `{"settings": {...}}` | per-lang `products_url_slug` under `translations` |
| `PUT /site` | flat | `{"title": "..."}` |

## Read-only fields (do not send on PUT)

- `article.body`, `article.title`, `article.excerpt` — write to `autosaved_*` instead
- `layout_asset.filename` — DELETE+POST workaround via `asset_replace`
- `page.public_url`, `page.path` (auto-derived from slug+parent), `page.created_at`, `page.updated_at`
- `product.in_stock`, `product.on_sale`, `product.effective_price`, `product.price_min/max`, `product.uses_variants` (computed)
- `site.code` (immutable once set + once site has paid plan)
- Any `data.internal_*` key (server-protected)

## Status enum quick reference

- `product.status`: `"draft"` | `"live"` (NOT `"active"`/`"published"`/`"hidden"`)
- `redirect_rule.redirect_type`: 301, 302, 307, 410
- `layout.content_type`: `page`, `blog`, `blog_article`, `elements`, `element`, `product`, `error_401`, `error_404`, `component`
- `page.content_type`: `page`, `link`, `blog`, etc.

## Cross-references

- Voog official docs index: <https://www.voog.com/developers/api>
- Project memory (skill): `.claude/skills/voog/SKILL.md`
- CHANGELOG entries for each tool: `CHANGELOG.md`
