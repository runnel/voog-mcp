# voog-mcp

CLI and MCP server for [Voog CMS](https://www.voog.com/) — manage Liquid templates, pages, products, ecommerce settings, and redirects from your terminal or directly from Claude / any MCP client.

## Install

While in v0.x development, install directly from GitHub:

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

This creates:
- `~/.config/voog/voog.json` — site registry
- `~/.config/voog/.env` (template) — where to put API tokens

Example `voog.json`:

```json
{
  "sites": {
    "mysite":   {"host": "mysite.com",   "api_key_env": "MYSITE_API_KEY"},
    "client_a": {"host": "clienta.com",  "api_key_env": "CLIENT_A_KEY"}
  },
  "default_site": "mysite"
}
```

Example `.env`:

```
MYSITE_API_KEY=...your token here...
CLIENT_A_KEY=...another token...
```

Get a token from your Voog admin: **Admin → API**.

### Per-repo site selection

In a repo dedicated to one Voog site, drop a `voog-site.json` to pin the site:

```json
{"site": "mysite"}
```

Now `voog pull` / `voog push` from that directory always target the right site, even if the global default differs.

## Use the CLI

```bash
voog --help                      # all commands
voog --site mysite products      # list products on mysite
voog pull                        # download templates (uses voog-site.json)
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

## License

MIT
