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

The cwd-level `voog.json` deep-merges over the home config, with cwd winning per-key. You can also redefine entire sites here (handy for client repos that should bring their own host/token without touching the home config):

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
voog --site mysite products      # list products on mysite
voog pull                        # download templates (uses cwd-level voog.json)
voog push layouts/Front\ page.tpl
voog redirects
voog config check                # verify all configured tokens
```

## Use as MCP server

Add to your Claude Code config (or any MCP client):

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

voog_get_page(site="mysite", page_id=42)
→ {...}
```

## What's NOT supported

voog-mcp covers the surface area needed to manage content and a small ecommerce catalog. The following Voog API areas are intentionally out of scope for now:

- Form definitions and form responses
- Comments and visitor data
- Site-level settings, languages, and translations
- Customer, order, and cart data (ecommerce orders/checkout flows)
- Users, roles, and permissions
- General asset/media library uploads (product images are supported)
- Site creation (voog-mcp targets existing sites)

If you need any of these, open an [issue](https://github.com/runnel/voog-mcp/issues) — or a PR.

## License

MIT
