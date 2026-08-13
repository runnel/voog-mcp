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
| Discovery | `voog_list_sites` | `voog_reload_config` | Lists configured `site` aliases from `voog.json` — call before any other tool to know what to pass as `site`. No HTTP request. `voog_reload_config` re-reads `voog.json` + `.env` and drops cached clients, so a site registered after the server started is usable without restarting the MCP host; per-site request counts survive the reload, and a reload that would leave zero sites is refused. |
| Pages | `pages_list`, `page_get` | `page_set_hidden`, `page_set_layout`, `page_delete`, `page_create`, `page_update`, `page_set_data`, `page_delete_data`, `page_duplicate` | `parent_id` is a page id, NOT node_id; root pages omit `parent_id`. Parallel translations use `node_id` (see Multilingual). `page_delete_data` requires `force=true`. `page_duplicate` returns the copy with `hidden=true` — follow up with `page_set_hidden(false)`. |
| Articles | `articles_list`, `article_get` | `article_create`, `article_update`, `article_publish`, `article_delete` | Use `autosaved_title/excerpt/body` on PUT; `publishing: true` to push autosaved → published. `description` ≠ `excerpt` (see skill memory). |
| Layouts | (resource only) | `layout_rename`, `layout_create`, `layout_update`, `layout_delete`, `asset_replace`, `layouts_pull`, `layouts_push` | `PUT /layouts/{id}` accepts `body` + `title` only. `layouts_pull`/`layouts_push` are bulk filesystem sync — clone all layouts + assets into a directory, edit locally, push back. |
| Layout assets | (resource only) | `layout_asset_create`, `layout_asset_update`, `layout_asset_upload`, `layout_asset_delete` | PUT `data` only — `filename` is read-only (use `asset_replace`). Text `data` crosses a JSON boundary, so literal `\uXXXX` escapes arrive decoded (#138): deploy tracked files with `layouts_push`, which reads from disk. `layout_asset_upload` is the BINARY route (multipart `POST /layout_assets`) for favicons, icons and fonts. |
| Texts | `text_get` | `text_update`, `page_add_content`, `article_add_content` | Page and article content bodies live here. Fresh pages/articles return `[]` from `/contents` until edit-mode trigger. Both add-content tools refuse a duplicate area name unless `force=true` — repeated names are legitimate on some article layouts. |
| Media library | `media_set_get` (galleries), otherwise resource only | `asset_upload` | 3-step protocol (`POST /assets` → PUT bytes → PUT confirm). Reuses a same-named asset by default (Voog auto-suffixes duplicates rather than overwriting). Waits for the async resizes via the API — never probe a derivative URL, a too-early request earns a CDN-cached 403. Build srcsets from the returned `sizes`, and check `sizes_complete`. **Derivative caps apply to the LONG SIDE, not the height** (v1.5): a resize exists exactly when `max(width, height) > cap` for cap ∈ 150/600/1280/2048, and the produced file sits on the cap with the other side following the aspect ratio. Verified against all 1032 images in the kolm-koma-2026 library, 2026-08-13. |
| Media sets | (resource only) | `media_set_get`, `media_set_update_asset_titles`, `media_set_set_assets` | `PUT /media_sets/{id}` is replace-not-merge — an omitted asset is unlinked (#120). `media_set_set_assets` sets the full ordered list and refuses to drop a current asset without `force=true`. Needs an explicit 1-based `position` per asset — array order alone is not a signal here — plus write-read-retry (v1.4.4). |
| Redirects | `redirects_list` | `redirect_add`, `redirect_update`, `redirect_delete` | redirect_type ∈ {301, 302, 307, 410}. |
| Languages | `languages_list` | `language_create`, `language_delete` | `language_delete` requires `force=true`. `language_move` / `language_enable_autodetect` deferred — niche; use passthrough. |
| Nodes | `nodes_list`, `node_get` | `node_update`, `node_move`, `node_relocate` | `node_move` uses `?parent_id=N&position=M` query params (not body). `node_relocate` accepts one of `before`/`after`/`parent_node_id`. `node_create`/`node_delete` deferred — not documented by Voog. |
| Site | `site_get` | `site_update`, `site_set_data`, `site_delete_data` | `site.code` immutable once set. `data.internal_*` keys read-only. `site_delete_data` requires `force=true`. |
| Cross-site clone | (none — `site_clone` reads the source, writes the target) | `site_clone` | Applies one configured site's content to another (issue #140 item 2). Phased and resumable via a `state_dir` of source-id → target-id maps; **dry run unless `force=true`**. Reads the source LIVE rather than from a `site_snapshot` directory, because a snapshot carries no per-article contents, no language-level contents and no layout_asset `data`. Copies layouts, layout assets, media, site settings + `site.data`, pages (hierarchy + menu order), content areas (texts + galleries) and articles. Does NOT copy ecommerce, elements, redirects, webhooks, forms or comments; languages are matched by `code`, never created. Voog will not let it set `created_at` / `published_at`, and duplicate article paths get auto-suffixed. |
| Snapshot | `pages_snapshot`, `site_snapshot` | (none) | Read-only bulk dumps. `pages_snapshot` walks all pages + per-page contents; `site_snapshot` adds articles + products + redirects + layouts. Both fetch in parallel (`max_workers=8`). `site_snapshot` accepts optional `overwrite=true` for automation/cron use (v1.3); default false preserves v1.2.x "refuse existing directory" contract. |
| Products | `products_list` (now accepts `category_id` — v1.4 P4 E4), `product_get` | `product_create`, `product_update` (full fields), `product_set_images`, `product_delete` (force-gated — v1.4 P4 E1), `product_duplicate` (v1.4 P4 E2), `products_bulk_action` (v1.4 P4 E3 — `{actions, target_ids}`, NOT per-row updates) | `product_create` requires `name`, `slug`, `price`. PUT/POST envelope is built by `_payloads.build_product_payload` — callers pass flat attributes, the helper wraps `{"product": {...}}`. `product_create` uses `asset_ids` (POST shape, list of int); `product_set_images` uses flat (no envelope). `products_bulk_action` uses a different shape than per-row PUT: body is `{actions:[{target_field, action, value, source_field?}], target_ids:[int] \| "all"}`; response is `{counters:{processed, failed}, processed_ids, failed_ids}`. No empirical batch cap observed up to 1001 target_ids; duplicates collapse server-side. |
| Categories | `categories_list`, `category_get` (v1.4 P4 E4) | `category_create`, `category_update`, `category_delete` (force-gated) (v1.4 P4 E4) | Envelope `{category: {...}}`. Writable fields verified empirically (Stella OLD, 2026-05-27): `name`, `slug`, `parent_id`. Voog does NOT accept `description` or `image_id` on categories — they get silently dropped from POST responses. |
| Orders | `orders_list`, `order_get` (v1.4 P4 E5) | (passthrough — order mutations carry finance/ops risk) | Filters on `orders_list`: `status`, `payment_status`, `created_after`, `created_before` (map to Voog `q.order.<attr>.$eq` / `.$gteq` / `.$lteq`). Both tools default to `include_pii=false`, which routes the response through `voog._payloads.redact_pii` (whitelist over blacklist — N4). Whitelist drops customer / billing_address / shipping_address / note / return_url / urls / gateway_transaction_id / shipping_method_option / external_shipment_attrs / custom_field_values. `shipping_method` is kept but inner-whitelisted (drops `option` since parcel-machine labels can reveal customer location). Real field names: `cart_rules_applied` (not `cart_rules`), `items_subtotal_amount` (not `items_total_amount`), no top-level `discount` object (discounts surface via `cart_rules_applied` + `total_discount_amount`). |
| Discounts | `discounts_list`, `discount_get` (v1.4 P4 E6a) | `discount_create`, `discount_update`, `discount_delete` (force-gated) (v1.4 P4 E6a) | Envelope `{discount: {...}}`. Writable fields verified empirically: `code`, `name`, `description`, `amount`, `amount_mode`, `discount_type`, `status`, `applies_to`, `valid_from`, `valid_to`, `redemption_limit`, `stackable`, `currency`. Real enum values (Voog rejects others with 422): `status` ∈ {open, closed}; `amount_mode` ∈ {net, percent}; `discount_type` ∈ {percentage, fixed}; `applies_to` ∈ {cart, …}. |
| Cart rules | `cart_rules_list`, `cart_rule_get` (v1.4 P4 E6b) | `cart_rule_create`, `cart_rule_update`, `cart_rule_delete` (force-gated) (v1.4 P4 E6b) | Envelope `{cart_rule: {...}}`. Required on create: `kind`, `target_kind`, `target_id`, `conditions[]`, `result{}`. Inner shapes pass-through to Voog: `conditions: [{value, comparator, field, value_type}, ...]`, `result: {value, field, value_type}`. Common partial updates: toggle `enabled`, change `position`. |
| Shipping | `shipping_methods_list`, `gateways_list` (v1.4 P4 E7) | (passthrough — create/update/delete are infrequent) | `shipping_methods_list` returns full `options[]` nested list for parcel-machine carriers (Omniva, SmartPost) — expect multi-KB payloads per method. `gateways_list` includes `code`, `name`, `enabled`, `enabled_methods[]`, `all_payment_methods[]`, `url`. |
| Ecommerce settings | `ecommerce_settings_get` | `ecommerce_settings_update` | Per-language `products_url_slug` lives in `translations`. |
| Elements | `elements_list`, `element_get`, `element_definitions_list` | `element_create`, `element_update`, `element_delete`, `element_move` | Bodies are FLAT (no envelope wrapper) per Voog docs. `element_create` accepts `element_definition_id` (preferred) or `element_definition_title`. `element_update` is partial (sends only supplied fields among `title`/`path`/`values`). `element_delete` requires `force=true`. `element_definitions_list` returns sorted property keys so callers see what fields each definition expects. `element_move` (v1.4 S13) reorders/reparents element INSTANCES — element_definition mutations remain passthrough (N5: different resource). |
| Webhooks | `webhooks_list` | `webhook_create`, `webhook_update`, `webhook_delete` | Flat bodies per Voog docs. `webhook_update` is partial. `webhook_delete` requires `force=true`. Voog target+event matrix (`ticket`/`form`/`order` × respective events) not enum-enforced — Voog rejects invalid combos with 422. |
| Content partials | (none — use `layouts_pull` to read) | `content_partial_update` | PUT to `/content_partials/{id}`. Flat body (`body` and/or `metainfo`). Requires at least one field. Avoids `layouts_pull`/`layouts_push` filesystem detour for targeted fragment edits. |
| Articles (data) | (via `article_get`) | `article_set_data`, `article_delete_data` | Symmetric with `page_set_data`/`page_delete_data`. Same `_validate_data_key` helper (rejects empty/whitespace, `internal_*` prefix, traversal chars). `article_delete_data` requires `force=true`. |
| Comments (articles) | `comments_list` | `comment_delete`, `comment_toggle_spam` | `GET /articles/{id}/comments`. `comment_toggle_spam` body is FLAT `{is_spam: bool}`. `comment_delete` requires `force=true`. Author/body/email edits via passthrough. |
| Tags | `tags_list`, `tag_get` | `tag_delete` | `GET /tags[,/{id}]`. Tags auto-created when articles reference them; explicit create/update via passthrough. `tag_delete` requires `force=true`. |
| Search | `voog_search` | — | `GET /admin/api/search?q=...&scope=...`. Returns flat hit list with `kind`. Indexing must be enabled site-side; MD5 sentinel detects when it isn't. PUBLIC content only; fresh edits + drafts not visible. |
| Me (account discovery) | `voog_list_my_sites` | — | `GET /admin/api/me/sites`. `token_env=` first-class (secret stays in env); `token=` fallback. Site-scoped — array length 1 always. |
| **Everything else** | `voog_admin_api_read`, `voog_ecommerce_api_read` | `voog_admin_api_call`, `voog_ecommerce_api_call` | Generic passthrough — same auth, same timeout, no envelope assumed. Use for forms, tickets, templates, imports and anything else without a typed tool. **Reads go to the `*_read` tools** (`readOnlyHint=true`, so MCP hosts may skip the destructive-action prompt); the `*_call` tools are write-only — `method='GET'` was deprecated in v1.4 and **removed in v1.5**, and passing it returns an error naming the read tool. |

## Endpoint × verb matrix

Per v1.4 design spec — every phase from v1.4 onward uses this column shape so coverage edits don't break earlier rows. ✓ = typed MCP tool exists; ✓ (merge) = PATCH route with merge semantics; — = no typed tool (use passthrough). Phase 1 only seeds the matrix shape; phases 2–7 fill in rows.

| Endpoint | GET | POST | PUT | PATCH | DELETE | Notes |
|---|---|---|---|---|---|---|
| `/layouts` | ✓ (list+detail via `layouts_pull`) | ✓ (`layout_create`) | ✓ (`layout_rename`, `layout_update`, `asset_replace`, `layouts_push`) | — | ✓ (`layout_delete`, force-gated) | `include_body=true` on list (v1.4 S1) |
| `/products` | ✓ (`products_list`, `product_get`) | ✓ (`product_create`) | ✓ (`product_update`, `product_set_images`) | — | — | List includes `variants,variant_types,translations` on snapshot path (v1.4 S2) |
| `/pages` | ✓ (`pages_list`, `page_get`) | ✓ (`page_create`) | ✓ (`page_update`, `page_set_hidden`, `page_set_layout`, `page_set_data`) | ✓ (merge) — `page_update(data=...)` (v1.4 S4) | ✓ (`page_delete`, force-gated; `page_delete_data`, force-gated) | `page_update(data=...)` routes via PATCH (merge) — S4 |
| `/articles` | ✓ (`articles_list`, `article_get`) | ✓ (`article_create`) | ✓ (`article_update`, `article_publish`, `article_set_data`) | ✓ (merge) — `article_update(data=...)` (v1.4 S4) | ✓ (`article_delete`, force-gated; `article_delete_data`, force-gated) | `article_update(data=...)` routes via PATCH (merge) — S4 |
| `/articles/{id}/comments` | ✓ (`comments_list`) | — | — | — | — | List comments on an article (read-only) — v1.4 S12. |
| `/articles/{id}/comments/{cid}` | — | — | ✓ (`comment_toggle_spam`) | — | ✓ (`comment_delete`, force-gated) | Spam-toggle PUT body `{is_spam: bool}` (flat); delete force-gated — v1.4 S12. |
| `/elements/{id}/move` | — | — | ✓ (`element_move`) | — | — | Instance reorder/reparent. QUERY-STRING params (NOT body, mirrors node_move): `page_id` (new parent page), `before` / `after` (sibling element id; mutually exclusive). At least one required. Idempotent — v1.4 S13/N5. Voog docs: https://www.voog.com/developers/api/resources/elements |
| `/me/sites` | ✓ (`voog_list_my_sites`) | — | — | — | — | Site-scoped — array length 1 always. `token_env=` preferred — v1.4 S6/R6. |
| `/search` | ✓ (`voog_search`) | — | — | — | — | Scope enum `pages\|articles\|elements\|products\|all`; MD5 sentinel detects indexing-off — v1.4 S5/MD5. |
| `/tags` | ✓ (`tags_list`) | — | — | — | — | Read-only listing — v1.4 S12. |
| `/tags/{id}` | ✓ (`tag_get`) | — | — | — | ✓ (`tag_delete`, force-gated) | Tags auto-created when referenced; explicit create/update via passthrough — v1.4 S12. |
| `/assets` | ✓ (via `asset_upload`'s reuse lookup) | ✓ (`asset_upload`) | ✓ (`asset_upload` — bytes + confirm) | — | — | 3-step protocol. Derivative caps are LONG-SIDE caps (v1.5). |
| `/layout_assets` | ✓ (`layouts_pull` — detail GET per editable asset; the LIST response carries no `data`) | ✓ (`layout_asset_create` text, `layout_asset_upload` binary/multipart) | ✓ (`layout_asset_update`, `asset_replace`, `layouts_push`) | — | ✓ (`layout_asset_delete`) | `filename` is read-only on PUT. Text `data` crosses a JSON boundary — deploy tracked files via `layouts_push` (#138). |
| `/media_sets/{id}` | ✓ (`media_set_get`) | — | ✓ (`media_set_set_assets`, `media_set_update_asset_titles`) | — | — | Replace-not-merge. Explicit 1-based `position` + write-read-retry required (v1.4.4). `POST /media_sets` returns 500 — a media_set must already exist. |
| **Generic passthrough** | ✓ (`voog_admin_api_read`, `voog_ecommerce_api_read`) | ✓ (`voog_admin_api_call`, `voog_ecommerce_api_call`) | ✓ (same) | ✓ (same) | ✓ (same) | Read/write split — v1.4 S3. The `*_call` tools are **write-only as of v1.5**: `method='GET'` is removed and returns an error naming the matching `*_read` tool. |

| `/products` (bulk) | — | — | ✓ (`products_bulk_action`, v1.4 P4 E3) — `{actions, target_ids}` shape | — | ✓ (`product_delete`, force-gated; `product_duplicate` via POST .../duplicate) | Empirical: no batch-size cap up to 1001 target_ids. |
| `/categories` | ✓ (`categories_list`, `category_get`) | ✓ (`category_create`) | ✓ (`category_update`) | — | ✓ (`category_delete`, force-gated) | Envelope `{category: {...}}`. Writable: name, slug, parent_id. v1.4 P4 E4. |
| `/orders` | ✓ (`orders_list`, `order_get`) — PII-redacted | — | — | — | — | Filters: status, payment_status, created_after, created_before. include_pii=false default. v1.4 P4 E5. |
| `/discounts` | ✓ (`discounts_list`, `discount_get`) | ✓ (`discount_create`) | ✓ (`discount_update`) | — | ✓ (`discount_delete`, force-gated) | Envelope `{discount: {...}}`. v1.4 P4 E6a. |
| `/cart_rules` | ✓ (`cart_rules_list`, `cart_rule_get`) | ✓ (`cart_rule_create`) | ✓ (`cart_rule_update`) | — | ✓ (`cart_rule_delete`, force-gated) | Envelope `{cart_rule: {...}}`. conditions[]+result{} pass-through. v1.4 P4 E6b. |
| `/shipping_methods` | ✓ (`shipping_methods_list`) | — | — | — | — | Read-only this phase. v1.4 P4 E7. |
| `/gateways` | ✓ (`gateways_list`) | — | — | — | — | Read-only this phase. v1.4 P4 E7. |

(Rows for `/webhooks`, `/redirect_rules`, `/nodes`, `/site`, `/texts`, `/content_partials` and `/languages` still to be added; every tool that exists is named somewhere in this document — `tests/test_coverage_doc.py` fails the build otherwise.)

Last verified against Voog API: 2026-08-13.

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
| `PUT /products/{id}` for image_id+assets | **flat, NOT wrapped** | `{"image_id": ..., "assets": [{"id": N}]}` — on PUT the field is `assets:[{id}]`, NOT `asset_ids` (POST-only). Sending `asset_ids` on PUT silently keeps only the hero image and drops the rest of the gallery; see the `voog.mcp.tools.products_images` module docstring. |
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

_Last verified against Voog API: 2026-08-13._
