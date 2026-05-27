# Voog MCP — endpoint coverage reference

This document maps Voog API endpoints to MCP tools and notes envelope shapes,
mutable fields, and gotchas. Maintained alongside `src/voog/mcp/tools/` —
update this doc when a tool is added or a new endpoint quirk is discovered.

## API surfaces

- **Admin API** — `https://{host}/admin/api/*`
- **Ecommerce v1 API** — `https://{host}/admin/api/ecommerce/v1/*`
- Auth: `X-API-Token: <token>` header (already handled by `VoogClient`)
- Pagination default: 50 / max 250; `voog.client.VoogClient.get_all` defaults to **250** per page (v1.4 MD1; was 200 in v1.3, 100 pre-1.3)
- Filter syntax: `q.<obj>.<attr>.<comp>=value` (`$eq`, `$cont`, `$gt`, …)
- Response shaping: `include=foo,bar`, `language_code=<iso>`

## Coverage matrix

| Resource | Read tools | Write tools | Notes |
|---|---|---|---|
| Discovery | `voog_list_sites` | (none) | Lists configured `site` aliases from `voog.json` — call before any other tool to know what to pass as `site`. No HTTP request. |
| Pages | `pages_list`, `page_get` | `page_set_hidden`, `page_set_layout`, `page_delete`, `page_create`, `page_update`, `page_set_data`, `page_delete_data`, `page_duplicate` | `parent_id` is a page id, NOT node_id; root pages omit `parent_id`. Parallel translations use `node_id` (see Multilingual). `page_delete_data` requires `force=true`. `page_duplicate` returns the copy with `hidden=true` — follow up with `page_set_hidden(false)`. |
| Articles | `articles_list`, `article_get` | `article_create`, `article_update`, `article_publish`, `article_delete` | Use `autosaved_title/excerpt/body` on PUT; `publishing: true` to push autosaved → published. `description` ≠ `excerpt` (see skill memory). |
| Layouts | (resource only) | `layout_rename`, `layout_create`, `layout_update`, `layout_delete`, `asset_replace`, `layouts_pull`, `layouts_push` | `PUT /layouts/{id}` accepts `body` + `title` only. `layouts_pull`/`layouts_push` are bulk filesystem sync — clone all layouts + assets into a directory, edit locally, push back. |
| Layout assets | (resource only) | `layout_asset_create`, `layout_asset_update`, `layout_asset_delete` | PUT `data` only — `filename` is read-only (use `asset_replace`). |
| Texts | `text_get` | `text_update`, `page_add_content` | Page content bodies live here. Fresh pages return `[]` from `/contents` until edit-mode trigger. |
| Redirects | `redirects_list` | `redirect_add`, `redirect_update`, `redirect_delete` | redirect_type ∈ {301, 302, 307, 410}. |
| Languages | `languages_list` | `language_create`, `language_delete` | `language_delete` requires `force=true`. `language_move` / `language_enable_autodetect` deferred — niche; use passthrough. |
| Nodes | `nodes_list`, `node_get` | `node_update`, `node_move`, `node_relocate` | `node_move` uses `?parent_id=N&position=M` query params (not body). `node_relocate` accepts one of `before`/`after`/`parent_node_id`. `node_create`/`node_delete` deferred — not documented by Voog. |
| Site | `site_get` | `site_update`, `site_set_data`, `site_delete_data` | `site.code` immutable once set. `data.internal_*` keys read-only. `site_delete_data` requires `force=true`. |
| Snapshot | `pages_snapshot`, `site_snapshot` | (none) | Read-only bulk dumps. `pages_snapshot` walks all pages + per-page contents; `site_snapshot` adds articles + products + redirects + layouts. Both fetch in parallel (`max_workers=8`). `site_snapshot` accepts optional `overwrite=true` for automation/cron use (v1.3); default false preserves v1.2.x "refuse existing directory" contract. |
| Products | `products_list` (now accepts `category_id` — v1.4 P4 E4), `product_get` | `product_create`, `product_update` (full fields), `product_set_images`, `product_delete` (force-gated — v1.4 P4 E1), `product_duplicate` (v1.4 P4 E2), `products_bulk_action` (v1.4 P4 E3 — `{actions, target_ids}`, NOT per-row updates) | `product_create` requires `name`, `slug`, `price`. PUT/POST envelope is built by `_payloads.build_product_payload` — callers pass flat attributes, the helper wraps `{"product": {...}}`. `product_create` uses `asset_ids` (POST shape, list of int); `product_set_images` uses flat (no envelope). `products_bulk_action` uses a different shape than per-row PUT: body is `{actions:[{target_field, action, value, source_field?}], target_ids:[int] \| "all"}`; response is `{counters:{processed, failed}, processed_ids, failed_ids}`. No empirical batch cap observed up to 1001 target_ids; duplicates collapse server-side. |
| Categories | `categories_list`, `category_get` (v1.4 P4 E4) | `category_create`, `category_update`, `category_delete` (force-gated) (v1.4 P4 E4) | Envelope `{category: {...}}`. Writable fields verified empirically (Stella OLD, 2026-05-27): `name`, `slug`, `parent_id`. Voog does NOT accept `description` or `image_id` on categories — they get silently dropped from POST responses. |
| Orders | `orders_list`, `order_get` (v1.4 P4 E5) | (passthrough — order mutations carry finance/ops risk) | Filters on `orders_list`: `status`, `payment_status`, `created_after`, `created_before` (map to Voog `q.order.<attr>.$eq` / `.$gteq` / `.$lteq`). Both tools default to `include_pii=false`, which routes the response through `voog._payloads.redact_pii` (whitelist over blacklist — N4). Whitelist drops customer / billing_address / shipping_address / note / return_url / urls / gateway_transaction_id / shipping_method_option / external_shipment_attrs / custom_field_values. `shipping_method` is kept but inner-whitelisted (drops `option` since parcel-machine labels can reveal customer location). Real field names: `cart_rules_applied` (not `cart_rules`), `items_subtotal_amount` (not `items_total_amount`), no top-level `discount` object (discounts surface via `cart_rules_applied` + `total_discount_amount`). |
| Discounts | `discounts_list`, `discount_get` (v1.4 P4 E6a) | `discount_create`, `discount_update`, `discount_delete` (force-gated) (v1.4 P4 E6a) | Envelope `{discount: {...}}`. Writable fields verified empirically: `code`, `name`, `description`, `amount`, `amount_mode`, `discount_type`, `status`, `applies_to`, `valid_from`, `valid_to`, `redemption_limit`, `stackable`, `currency`. Real enum values (Voog rejects others with 422): `status` ∈ {open, closed}; `amount_mode` ∈ {net, percent}; `discount_type` ∈ {percentage, fixed}; `applies_to` ∈ {cart, …}. |
| Cart rules | `cart_rules_list`, `cart_rule_get` (v1.4 P4 E6b) | `cart_rule_create`, `cart_rule_update`, `cart_rule_delete` (force-gated) (v1.4 P4 E6b) | Envelope `{cart_rule: {...}}`. Required on create: `kind`, `target_kind`, `target_id`, `conditions[]`, `result{}`. Inner shapes pass-through to Voog: `conditions: [{value, comparator, field, value_type}, ...]`, `result: {value, field, value_type}`. Common partial updates: toggle `enabled`, change `position`. |
| Shipping | `shipping_methods_list`, `gateways_list` (v1.4 P4 E7) | (passthrough — create/update/delete are infrequent) | `shipping_methods_list` returns full `options[]` nested list for parcel-machine carriers (Omniva, SmartPost) — expect multi-KB payloads per method. `gateways_list` includes `code`, `name`, `enabled`, `enabled_methods[]`, `all_payment_methods[]`, `url`. |
| Ecommerce settings | `ecommerce_settings_get` | `ecommerce_settings_update` | Per-language `products_url_slug` lives in `translations`. |
| Elements | `elements_list`, `element_get`, `element_definitions_list` | `element_create`, `element_update`, `element_delete` | Bodies are FLAT (no envelope wrapper) per Voog docs. `element_create` accepts `element_definition_id` (preferred) or `element_definition_title`. `element_update` is partial (sends only supplied fields among `title`/`path`/`values`). `element_delete` requires `force=true`. `element_definitions_list` returns sorted property keys so callers see what fields each definition expects. Element reposition (`PUT /elements/{id}/move`) and element_definition mutations deferred — use passthrough. |
| Webhooks | `webhooks_list` | `webhook_create`, `webhook_update`, `webhook_delete` | Flat bodies per Voog docs. `webhook_update` is partial. `webhook_delete` requires `force=true`. Voog target+event matrix (`ticket`/`form`/`order` × respective events) not enum-enforced — Voog rejects invalid combos with 422. |
| Content partials | (none — use `layouts_pull` to read) | `content_partial_update` | PUT to `/content_partials/{id}`. Flat body (`body` and/or `metainfo`). Requires at least one field. Avoids `layouts_pull`/`layouts_push` filesystem detour for targeted fragment edits. |
| Articles (data) | (via `article_get`) | `article_set_data`, `article_delete_data` | Symmetric with `page_set_data`/`page_delete_data`. Same `_validate_data_key` helper (rejects empty/whitespace, `internal_*` prefix, traversal chars). `article_delete_data` requires `force=true`. |
| **Everything else** | `voog_admin_api_call(method, path, ...)` | `voog_ecommerce_api_call(method, path, ...)` | Generic passthrough — same auth, same timeout, no envelope assumed. Use for orders, carts, discounts, gateways, shipping_methods, forms, tickets, tags, media_sets, templates, bulk update, imports, search. |

## Endpoint × verb matrix

Per v1.4 design spec — every phase from v1.4 onward uses this column shape so coverage edits don't break earlier rows. ✓ = typed MCP tool exists; ✓ (merge) = PATCH route with merge semantics; — = no typed tool (use passthrough). Phase 1 only seeds the matrix shape; phases 2–7 fill in rows.

| Endpoint | GET | POST | PUT | PATCH | DELETE | Notes |
|---|---|---|---|---|---|---|
| `/layouts` | ✓ (list+detail via `layouts_pull`) | ✓ (`layout_create`) | ✓ (`layout_rename`, `layout_update`, `asset_replace`, `layouts_push`) | — | ✓ (`layout_delete`, force-gated) | `include_body=true` on list (v1.4 S1) |
| `/products` | ✓ (`products_list`, `product_get`) | ✓ (`product_create`) | ✓ (`product_update`, `product_set_images`) | — | — | List includes `variants,variant_types,translations` on snapshot path (v1.4 S2) |
| `/pages` | ✓ (`pages_list`, `page_get`) | ✓ (`page_create`) | ✓ (`page_update`, `page_set_hidden`, `page_set_layout`, `page_set_data`) | ✓ (merge) — `page_update(data=...)` (v1.4 S4) | ✓ (`page_delete`, force-gated; `page_delete_data`, force-gated) | `page_update(data=...)` routes via PATCH (merge) — S4 |
| `/articles` | ✓ (`articles_list`, `article_get`) | ✓ (`article_create`) | ✓ (`article_update`, `article_publish`, `article_set_data`) | ✓ (merge) — `article_update(data=...)` (v1.4 S4) | ✓ (`article_delete`, force-gated; `article_delete_data`, force-gated) | `article_update(data=...)` routes via PATCH (merge) — S4 |

| `/products` (bulk) | — | — | ✓ (`products_bulk_action`, v1.4 P4 E3) — `{actions, target_ids}` shape | — | ✓ (`product_delete`, force-gated; `product_duplicate` via POST .../duplicate) | Empirical: no batch-size cap up to 1001 target_ids. |
| `/categories` | ✓ (`categories_list`, `category_get`) | ✓ (`category_create`) | ✓ (`category_update`) | — | ✓ (`category_delete`, force-gated) | Envelope `{category: {...}}`. Writable: name, slug, parent_id. v1.4 P4 E4. |
| `/orders` | ✓ (`orders_list`, `order_get`) — PII-redacted | — | — | — | — | Filters: status, payment_status, created_after, created_before. include_pii=false default. v1.4 P4 E5. |
| `/discounts` | ✓ (`discounts_list`, `discount_get`) | ✓ (`discount_create`) | ✓ (`discount_update`) | — | ✓ (`discount_delete`, force-gated) | Envelope `{discount: {...}}`. v1.4 P4 E6a. |
| `/cart_rules` | ✓ (`cart_rules_list`, `cart_rule_get`) | ✓ (`cart_rule_create`) | ✓ (`cart_rule_update`) | — | ✓ (`cart_rule_delete`, force-gated) | Envelope `{cart_rule: {...}}`. conditions[]+result{} pass-through. v1.4 P4 E6b. |
| `/shipping_methods` | ✓ (`shipping_methods_list`) | — | — | — | — | Read-only this phase. v1.4 P4 E7. |
| `/gateways` | ✓ (`gateways_list`) | — | — | — | — | Read-only this phase. v1.4 P4 E7. |

(Rows for `/elements`, `/webhooks`, `/redirect_rules`, `/nodes`, `/site`, `/texts`, `/content_partials`, `/languages`, `/layout_assets`, `/me` added in subsequent v1.4 phases.)

Last verified against Voog API: 2026-05-27.

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
| `POST /products`, `PUT /products/{id}` | `{"product": {...}}` | Built by `_payloads.build_product_payload`; translations / fields nested inside. |
| `PUT /products/{id}` for image_id+asset_ids | **flat, NOT wrapped** | `{"image_id": ..., "asset_ids": [...]}` (empirically confirmed in `product_set_images`) |
| **`PUT /products` (bulk action)** | **flat with `actions` + `target_ids`** | `{"actions":[{"target_field":"status","action":"set","value":"draft"}], "target_ids":[1,2,3]}` — NOT a per-row updates list. Response: `{counters:{processed, failed}, processed_ids, failed_ids}`. |
| `POST/PUT /categories`, `POST/PUT /discounts`, `POST/PUT /cart_rules` | `{"<singular>": {...}}` | `category` / `discount` / `cart_rule` envelope wrappers (v1.4 Phase 4). |
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

---

_Last verified against Voog API: 2026-05-27._
