# voog-mcp

[![PyPI](https://img.shields.io/pypi/v/voog-mcp.svg)](https://pypi.org/project/voog-mcp/)
[![tests](https://github.com/runnel/voog-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/runnel/voog-mcp/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.10 | 3.11 | 3.12](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)

CLI and MCP server for [Voog CMS](https://www.voog.com/) — manage Liquid templates, pages, products, ecommerce settings, and redirects from your terminal or directly from Claude / any MCP client.

## What is Voog?

[Voog](https://www.voog.com/) is a multilingual website builder and CMS with built-in ecommerce, used for content sites and small online stores. This package wraps its [admin API](https://www.voog.com/developers) so you can edit templates, pages, products, and redirects from your shell or an LLM agent.

## Install

From PyPI:

```bash
pip install voog-mcp
# or, no install: uvx voog-mcp --help
```

Or directly from GitHub (latest unreleased main):

```bash
uvx --from git+https://github.com/runnel/voog-mcp.git voog --help
```

For development:

```bash
git clone https://github.com/runnel/voog-mcp
cd voog-mcp
python3.10 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

Run `voog config init` to interactively create the global config:

```bash
voog config init
```

This creates `~/.config/voog/voog.json` with your tokens inline:

```json
{
  "sites": {
    "mysite":   {"host": "mysite.com",   "api_key": "vk_..."},
    "client_a": {"host": "clienta.com",  "api_key": "vk_..."}
  },
  "default_site": "mysite"
}
```

Get a token from your Voog admin: **Admin → API**.

### Shared / CI configs

If `voog.json` is checked into version control or shared across machines, keep the token out of the file by referencing an env var instead:

```json
{
  "sites": {
    "client_a": {"host": "clienta.com", "api_key_env": "CLIENT_A_KEY"}
  }
}
```

Then put the token in `~/.config/voog/.env`:

```
CLIENT_A_KEY=vk_...
```

Both forms can coexist per-site. When both `api_key` and `api_key_env` are set, the env-var wins if it's defined — so an inline value acts as a default that the deployment overrides.

### Per-repo site selection

In a repo dedicated to one Voog site, drop a `voog.json` at the repo root to pin the site:

```json
{"default_site": "mysite"}
```

The cwd-level `voog.json` deep-merges over the home config, with cwd winning per-key. Inside `sites`, the merge is per-site name — a cwd entry replaces the whole site definition (host + token), it does not merge individual fields. You can also redefine entire sites here (handy for client repos that should bring their own host/token without touching the home config):

```json
{
  "sites": {
    "client_x": {"host": "clientx.com", "api_key": "vk_..."}
  },
  "default_site": "client_x"
}
```

Now `voog pull` / `voog push` from that directory always target the right site, even if the home default differs.

> **Note:** `voog-site.json` from earlier versions still works but emits a `DeprecationWarning`. Replace it with `voog.json` containing `{"default_site": "<name>"}` for the same effect.

## Use the CLI

```bash
voog --help                      # all commands
voog config list-sites           # show configured sites
voog --site mysite products      # list products on mysite
voog pull                        # download templates (uses cwd-level voog.json)
voog push layouts/Front\ page.tpl
voog redirects
voog config check                # verify all configured tokens
voog site-snapshot backup/       # full-site snapshot for diff/audit
```

## Use as MCP server

Add to your Claude Code config (or any MCP client). The simplest setup uses the published PyPI package:

```json
{
  "mcpServers": {
    "voog": {
      "command": "uvx",
      "args": ["voog-mcp"]
    }
  }
}
```

If you'd rather track unreleased main (e.g. for a fix that hasn't shipped yet), point `uvx` at the GitHub repo instead:

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

Every tool requires a `site` parameter. Start with `voog_list_sites` to discover what's configured:

```
voog_list_sites()
→ [{"name": "mysite", "host": "mysite.com"}, ...]

page_get(site="mysite", page_id=42)
→ {...}
```

## Tools

Full endpoint coverage reference: [docs/voog-mcp-endpoint-coverage.md](docs/voog-mcp-endpoint-coverage.md)

| Group | Tools |
|---|---|
| Sites | `voog_list_sites`, `voog_list_my_sites`, `voog_reload_config` (pick up sites added after the server started, without restarting the MCP host) |
| Search | `voog_search` |
| Pages | `pages_list`, `page_get`, `page_create`, `page_update`, `page_set_hidden`, `page_set_layout`, `page_set_data`, `page_delete_data`, `page_duplicate`, `page_delete` |
| Articles | `articles_list`, `article_get`, `article_create`, `article_update`, `article_publish`, `article_set_data`, `article_delete_data`, `article_delete` |
| Comments | `comments_list`, `comment_delete`, `comment_toggle_spam` |
| Tags | `tags_list`, `tag_get`, `tag_delete` |
| Layouts | `layouts_pull`, `layouts_push`, `layout_create`, `layout_update`, `layout_rename`, `layout_delete`, `layout_asset_create`, `layout_asset_update`, `layout_asset_upload` (binary: favicons, fonts, icons — multipart), `layout_asset_delete`, `asset_replace` |
| Texts / contents | `text_get`, `text_update`, `page_add_content`, `article_add_content`, `content_partial_update` |
| Elements | `elements_list`, `element_get`, `element_definitions_list`, `element_create`, `element_update`, `element_move`, `element_delete` |
| Products | `products_list`, `product_get`, `product_create`, `product_update`, `product_set_images`, `product_delete`, `product_duplicate`, `products_bulk_action` |
| Categories | `categories_list`, `category_get`, `category_create`, `category_update`, `category_delete` |
| Media library | `asset_upload` (unattached image upload — reuses a same-named asset instead of letting Voog auto-suffix a duplicate, waits for the async resizes, returns the derivative sizes Voog actually made) |
| Media sets (galleries) | `media_set_get`, `media_set_update_asset_titles` (safe GET-then-PUT — `PUT /media_sets/{id}` is replace-not-merge), `media_set_set_assets` (build/reorder a gallery; refuses to drop images without `force`) |
| Orders | `orders_list`, `order_get` (read-only; PII-stripped by default, `include_pii=true` requires `force=true`) |
| Discounts | `discounts_list`, `discount_get`, `discount_create`, `discount_update`, `discount_delete` |
| Cart rules | `cart_rules_list`, `cart_rule_get`, `cart_rule_create`, `cart_rule_update`, `cart_rule_delete` |
| Shipping / payments | `shipping_methods_list`, `gateways_list` |
| Ecommerce settings | `ecommerce_settings_get`, `ecommerce_settings_update` |
| Multilingual | `languages_list`, `language_create`, `language_delete`, `nodes_list`, `node_get`, `node_update`, `node_move`, `node_relocate` |
| Redirects | `redirects_list`, `redirect_add`, `redirect_update`, `redirect_delete` |
| Site | `site_get`, `site_update`, `site_set_data`, `site_delete_data` |
| Webhooks | `webhooks_list`, `webhook_create`, `webhook_update`, `webhook_delete` |
| Snapshot | `pages_snapshot`, `site_snapshot` |
| **Read-only passthrough** | `voog_admin_api_read`, `voog_ecommerce_api_read` |
| **Generic passthrough (writes)** | `voog_admin_api_call`, `voog_ecommerce_api_call` — POST/PUT/PATCH/DELETE only. `method='GET'` was removed in v1.5; use the `_read` tools above. |

## What's NOT supported

voog-mcp covers content + ecommerce catalog management end-to-end as of v1.4. The following Voog API areas remain out of scope — drop down to the passthrough tools when you need them — `voog_admin_api_read` / `voog_ecommerce_api_read` to read, `voog_admin_api_call` / `voog_ecommerce_api_call` to write:

- **Order mutation** — `orders_list` / `order_get` are read-only typed tools (with PII stripping); creating / updating / cancelling orders goes via passthrough. Order writes carry finance / operations risk that a future release will design separately.
- **Cart reads** — `cart_rules_*` tools cover cart-rule CRUD, but reading individual cart sessions (`/carts`) is passthrough-only.
- **`element_definitions` CRUD** — `element_definitions_list` is wrapped; create / update / delete remain passthrough.
- **People / site_user admin** — full passthrough.
- **Form definitions and form responses** — passthrough.
- **Site favicons and bulk file imports** — product image galleries are first-class via `product_set_images`; other multipart uploads go via passthrough.
- **Site creation** — voog-mcp targets existing sites.

If you need any of these, open an [issue](https://github.com/runnel/voog-mcp/issues) — or a PR.

## License

MIT
