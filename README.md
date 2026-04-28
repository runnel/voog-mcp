# voog-mcp

Voog CMS automatiseerimise tööriistad — kasutusel mitme Voog saidi haldamisel (Stella Soomlais, Tõnu Runnel jt).

Repo sisaldab kaht eraldi tööriista:

- **`voog-mcp`** — MCP server, mis pakub Voog CMS-i (Liquid template'id, lehed, artiklid, tooted, ümbersuunamised) Claude'ile MCP tools + resources kujul
- **`voog.py`** — single-file Python CLI sama API jaoks, mõeldud terminalist ja batch-skriptidest kasutamiseks

Mõlemad räägivad sama Voog Admin API + Ecommerce v1 API'ga, jagavad sama autentimist (`X-API-Token`), ja on disainitud paralleelselt elama.

---

## voog-mcp (MCP server)

MCP server stdio transport peal, mis avab Voog CMS-i toimingud Claude Code'ile (ja teistele MCP klientidele) struktureeritud tools + resources kujul. Vt täisspetsifikatsioon: [docs/specs/2026-04-26-mcp-server.md](docs/specs/2026-04-26-mcp-server.md).

### Paigaldus

Vajab Python 3.10+. MCP SDK tuleb dependency'na automaatselt.

```bash
cd ~/path/to/voog-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

Pärast seda on saadaval:
- Käsk `voog-mcp` (vt `pyproject.toml` `[project.scripts]`)
- Importable `voog_mcp` pakett

### Seadistamine

`voog-mcp` loeb keskkonnamuutujaid:

| Muutuja | Tähendus |
|---|---|
| `VOOG_HOST` | Saidi hostname (nt `runnel.ee`, `stellasoomlais.com`) |
| `VOOG_API_TOKEN` | Voog Admin API token (`X-API-Token` päise jaoks) |

`claude_desktop_config.json` näide — üks server-entry sait kohta:

```json
{
  "mcpServers": {
    "voog-runnel": {
      "command": "/path/to/voog-mcp/.venv/bin/voog-mcp",
      "env": {
        "VOOG_HOST": "runnel.ee",
        "VOOG_API_TOKEN": "..."
      }
    },
    "voog-stella": {
      "command": "/path/to/voog-mcp/.venv/bin/voog-mcp",
      "env": {
        "VOOG_HOST": "stellasoomlais.com",
        "VOOG_API_TOKEN": "..."
      }
    }
  }
}
```

Iga saidi server-entry saab oma prefiksiga tool-name'id (`mcp__voog-runnel__pages_list`, `mcp__voog-stella__pages_list`) — Claude eristab saidid ühemõtteliselt isegi kui sama tool-name on mitmes server-entry'is.

### Tools

Kõik tools on registreeritud `voog_mcp/server.py` `TOOL_GROUPS` registris. Iga tool kannab eksplitsiitset MCP annotatsioonide kolmikut (`readOnlyHint`, `destructiveHint`, `idempotentHint`).

| Group (file) | Tool | Annotation | Kirjeldus |
|---|---|---|---|
| `pages.py` | `pages_list` | read-only | Kõik lehed (id, path, title, hidden, layout) |
| `pages.py` | `page_get` | read-only | Lehe täisinfo id järgi |
| `pages_mutate.py` | `page_set_hidden` | mutating, idempotent | Bulk `hidden` flag'i toggle ühele või mitmele lehele |
| `pages_mutate.py` | `page_set_layout` | mutating, idempotent | Lehe layouti vahetus |
| `pages_mutate.py` | `page_delete` | **destructive**, non-idempotent | Lehe kustutamine (vajab `force=true`) |
| `layouts.py` | `layout_rename` | mutating, idempotent | Layouti ümbernimetamine (id säilib) |
| `layouts.py` | `layout_create` | mutating, non-idempotent | Uus layout/component (POST `/layouts`) |
| `layouts.py` | `asset_replace` | mutating, non-idempotent | Layout asseti rename DELETE+POST workaround'iga |
| `layouts_sync.py` | `layouts_pull` | mutating-disk, idempotent | Tõmbab kõik layoutid ja komponendid `target_dir`-i (.tpl + manifest.json) |
| `layouts_sync.py` | `layouts_push` | mutating, idempotent | Push'b `target_dir` manifestist (kogu tree või filtreeritud `files=[...]`) |
| `snapshot.py` | `pages_snapshot` | mutating-disk, idempotent | Backup pages + per-page contents JSON-iks |
| `snapshot.py` | `site_snapshot` | mutating-disk, idempotent | Comprehensive backup IGAST mutable resource'ist (pre-flight enne ohtlikku op'i) |
| `products.py` | `products_list` | read-only | Kõik ecommerce tooted (lihtsustatud) |
| `products.py` | `product_get` | read-only | Toote täisinfo (variant_types + translations) |
| `products.py` | `product_update` | mutating, idempotent | Translation-keyed väljade uuendus (name, slug per keel) |
| `products_images.py` | `product_set_images` | **destructive**, non-idempotent | Toote piltide asendus (3-step asset upload + product PUT, vajab `force=true`) |
| `redirects.py` | `redirects_list` | read-only | Kõik redirect rules |
| `redirects.py` | `redirect_add` | mutating, non-idempotent | Lisa redirect rule (source → destination) |

**Annotatsioonide tähendus klientides:** `destructiveHint=true` käivitab Claude'is kasutaja kinnitusprompti enne tool'i käivitamist. Lisaks rakendab server in-band `force=true` checki — annotatsioon on UX-vihje, `force` on serveripoolne teine kaitsekiht.

### Resources

| URI | MIME type | Sisu |
|---|---|---|
| `voog://pages` | `application/json` | Lihtsustatud pages list (sama kuju nagu `pages_list`) |
| `voog://pages/{id}` | `application/json` | Üks leht — täisinfo |
| `voog://pages/{id}/contents` | `application/json` | Lehe content blokkide nimekiri |
| `voog://layouts` | `application/json` | Kõik layoutid (id, title, component, content_type, updated_at — **ilma body'ta**) |
| `voog://layouts/{id}` | `text/plain` | Üks layout — toores Liquid template (`.tpl` source) |
| `voog://articles` | `application/json` | Kõik blog-artiklid (ilma body'ta) |
| `voog://articles/{id}` | `text/html` | Üks artikkel — renderdatud HTML body |
| `voog://products` | `application/json` | Kõik tooted (`?include=translations`) |
| `voog://products/{id}` | `application/json` | Üks toode (`?include=variant_types,translations`) |
| `voog://redirects` | `application/json` | Kõik redirect rules |

Resource-ide kasutusmuster: vaata `voog_mcp/resources/` mooduleid. Vt täisspetsifikatsioon § 5.

### Kaks API surface'i

Voogil on kaks eraldi API base URL'i. MCP tools/resources käsitlevad seda läbipaistvalt:

- **Admin API** (`/admin/api/`) — pages, layouts, articles, redirects, assets
- **Ecommerce v1 API** (`/admin/api/ecommerce/v1/`) — products, settings

Klient (`voog_mcp.client.VoogClient`) pakub mõlemat base URL'i (`client.base_url`, `client.ecommerce_url`); ecommerce-specific tools ja resources kasutavad teist.

---

## voog.py (CLI)

Single-file Python CLI sama API jaoks. Pull/push template'id, hallata products, redirects, pages otse terminalist. Sõltuvused: ainult Python 3 stdlib (urllib, json, pathlib).

```bash
cd ~/path/to/voog-site-repo  # peab sisaldama voog-site.json'i
python3 ~/path/to/voog.py help
```

Vajab:
- `voog-site.json` igal saidikaustal: `{"host": "...", "api_key_env": "..."}` (turvameede saitide segiajamise vastu — voog.py keeldub töötamast ilma selleta)
- `.env` fail koos API key'dega (otsib `.env` voog.py kõrvalt, töökaustast, parent'idest)

Vt voog.py docstring kõikide käskude jaoks.

### Millal MCP, millal CLI

| Ülesanne | Soovitus |
|---|---|
| "Show me all pages with hidden=true" | MCP — `pages_list` (Claude conversational flow) |
| "Rename layout X to Y" | MCP — `layout_rename` |
| "Backup before risky op" | MCP — `site_snapshot` |
| Local file push/pull workflow git-iga | CLI — `voog.py pull` + `voog.py push` |
| Batch-skriptid, cron, CI | CLI — stable JSON output, scriptable |
| Buy Together avariinupp ja kiirfix'id | CLI — kiirem, vähem context'i |

MCP ja CLI on disainitud koos eksisteerima — sama manifest format (`layouts_pull` ↔ `voog.py pull`), sama snapshot kuju (`site_snapshot` ↔ `voog.py site-snapshot`).

---

## Arendus

### Testid

```bash
.venv/bin/python -m unittest discover tests
```

Testid kasutavad stdlib `unittest` + `unittest.mock` (no pytest dependency).

### Integration testid (RUN_SMOKE)

`tests/test_mcp_integration.py` testid spawnavad `voog-mcp` subprocess'i ja teevad päris API kõnesid `runnel.ee` vastu. Neid ei käivitata vaikimisi:

```bash
# Tavaline test run — integration testid skipitakse:
.venv/bin/python -m unittest discover tests

# Live API test run — vajab RUN_SMOKE=1 env'is. API võti loetakse
# AINULT failist Claude/.env (`RUNNEL_VOOG_API_KEY=...`), mitte shell env'ist
# — vt tests/test_mcp_integration.py:_read_smoke_api_key. Kui Claude/.env
# pole olemas või võtit pole, skippivad kõik smoke-testid vaikselt:
RUN_SMOKE=1 .venv/bin/python -m unittest tests.test_mcp_integration -v
```

CI's tuleb `RUN_SMOKE` jätta määramata.

### Spec ja plaan

- Spec: [docs/specs/2026-04-26-mcp-server.md](docs/specs/2026-04-26-mcp-server.md)
- Plan: [docs/plans/2026-04-26-mcp-server-plan.md](docs/plans/2026-04-26-mcp-server-plan.md)

### Skill

Voog'i tehniliste detailide kohta (API gotchas, render modes, page/article/element comparison, MCP server kasutus) — vt `~/.claude/skills/voog/SKILL.md`.
