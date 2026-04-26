# voog-mcp: CLI → MCP Server (Spec)

**Status:** spec, ootab implementatsiooni
**Spec kuupäev:** 2026-04-26
**Repo:** https://github.com/runnel/voog-mcp
**Implementeerimisplaan:** [`docs/plans/2026-04-26-mcp-server-plan.md`](../plans/2026-04-26-mcp-server-plan.md)

## 1. Eesmärk

Pordi olemasolev `voog.py` CLI päris MCP serveriks (Model Context Protocol), nii et Claude (ja teised MCP-kliendid) saavad Voog CMS saite hallata otse — ilma et inimene käivitaks bash-käske `voog.py`-st.

**Tulemus:**
- Claude Desktop / Claude Code / muud MCP-kliendid saavad `voog-mcp` serveri `claude_desktop_config.json`-is registreerida
- Iga Voog sait = oma server-instance (`voog-stella`, `voog-runnel`, …)
- Kõik 17 olemas-olevat CLI käsku saadaval MCP tool'idena
- Olulisemad andmekogud (pages, layouts, articles, products) eksponeeritud MCP resource'idena (Claude saab passiivselt lugeda)
- Olemas-olev CLI `voog.py` säilib backward-compat shim'ina (ei murdu).

**Mitte-eesmärk:** UI muudatused, uued Voog API funktsioonid, multi-tenant server. Ainult protokolli-pordilemine + lähetuspuhtus.

## 2. Motivatsioon

`voog-mcp` repo nime taga on praegu CLI script. Päris MCP server lubab:
- **Loomulik kasutus Claude'is** — "Vaheta runnel.ee root layout" → Claude valib õige tool'i, parameetrid, kutsub välja, parsib vastuse. Mitte: "kirjuta mulle bash käsk".
- **Resource-põhine kontekst** — Claude loeb passiivselt `voog://pages` ja teab struktuuri ilma explicit tool call'ita. Loogilisem migratsiooni-tüüpi tööde puhul.
- **Cross-projekt taaskasutus** — sama server töötab Stella-l, Runnelil, klientide saitidel. CLI seda ei lubanud (cwd-põhine).
- **Tool annotations** — `destructiveHint: true` paneb Claude'i kasutaja-kinnitust küsima `page-delete`-i puhul.
- **Skaleerumine** — pikemajalised operatsioonid (snapshot 85 lehte) saavad progress notification'eid; resource'id cached; jne.

## 3. Arhitektuursed otsused

### 3.1. MCP SDK kasutus (mitte raw protocol)

**Otsus:** Kasutame Anthropic'u [`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk).

**Põhjendus:** Stdlib-only filosoofia oli mõistlik CLI puhul, aga MCP protokoll on liiga keerukas käsitsi-ehituseks (JSON-RPC 2.0 edge cases, capabilities negotiation, resource templates, progress notifications). SDK on official, hooldatav, õpetlikum.

**Trade-off:** Lisab `mcp` Python paketi sõltuvuseks (esimene external dep). Aktsepteerime — see on eraldiseisvuse-vääriline kompromiss ühe-tööriistaspetsiifilise paketi puhul.

### 3.2. Üks server per sait (mitte multi-tenant)

**Otsus:** Iga Voog sait registreeritakse `claude_desktop_config.json`-is eraldi server-instancena (`voog-stella`, `voog-runnel`).

**Põhjendus:**
- Isoleeritud API key per server (kompromituse risk minimeeritud)
- Lihtsam mental model — server identity = sait identity
- Voog'is saidid täiesti eraldi (eraldi konto, billing) — multi-tenant pole tõeline use case
- Kasutaja praegu kasutab 2 saiti, multi-tenant lisab keerukust ilma kasuta

**Hilisem laiend:** kui kunagi vaja multi-site (nt agentuur Voog'iga), saab lisada site-arg tool'idele. Praegu mitte.

### 3.3. Tools vs Resources jaotus

| Operatsioon | MCP konstrukt | Põhjus |
|---|---|---|
| Mutate (push, rename, delete, set-*, snapshot, redirect-add, product-update) | **Tool** | Claude kutsub explicit'selt, need ei juhtu "passiivselt" |
| List (pages, products, redirects) | **Tool + Resource** | Tool: "anna mulle kõik pages". Resource: passive list browsing |
| Single read (page, layout body, article body, product details) | **Resource** ainult | Claude loeb URI järgi (`voog://pages/152377`), pole tool call'i tarvis |
| Pull, push (asset/layout sync) | **Tool** | Action-laadne, klient ootab tagasi diff'i / staatust |

### 3.4. Pakendus (package, mitte single-file)

**Otsus:** `voog.py` lahutatakse `voog_mcp/` Python pakettiks:

```
voog-mcp/                              ← repo root
├── pyproject.toml                     ← installable package config
├── README.md
├── voog.py                            ← legacy CLI shim (importib voog_mcp.client'ist)
├── voog_mcp/
│   ├── __init__.py                   ← versioon, exports
│   ├── __main__.py                   ← `python3 -m voog_mcp` entry
│   ├── server.py                     ← MCP server setup, capabilities
│   ├── config.py                     ← env vars / config loading
│   ├── client.py                     ← Voog API client (extracted from voog.py)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── pages.py                  ← pages_list, page_get tools
│   │   ├── pages_mutate.py           ← page_set_hidden, page_set_layout, page_delete
│   │   ├── layouts.py                ← layout_rename, push, pull
│   │   ├── snapshot.py               ← pages_snapshot
│   │   ├── products.py               ← products_list, product_get, product_update, product_set_images
│   │   └── redirects.py              ← redirects_list, redirect_add
│   ├── resources/
│   │   ├── __init__.py
│   │   ├── pages.py                  ← voog://pages, voog://pages/{id}
│   │   ├── layouts.py                ← voog://layouts/{id}
│   │   ├── articles.py               ← voog://articles/{id}
│   │   └── products.py               ← voog://products/{id}
│   └── prompts/
│       ├── __init__.py
│       └── migrate_layout.py         ← (optional v2)
└── tests/
    ├── test_voog.py                  ← olemas-olevad CLI-tasandi unit testid
    ├── test_client.py                ← API client unit testid
    ├── test_tools_pages.py
    ├── test_tools_layouts.py
    └── test_mcp_integration.py       ← mcp-inspector-style end-to-end testid
```

**Põhjendus:**
- 17 tool'i + 5 resource handler'it + server setup ei mahu hõlpsasti ühte ~900-rea faili
- Tool'i defineerimine vajab schema'sid (JSON Schema parameetritele) — kui need fail-grupiti, kergem hallata
- Tests kahekordistuvad (CLI + MCP) — paketi-struktuur skaleerub paremini

### 3.5. Backward compat: `voog.py` jääb tööle

**Otsus:** `voog.py` kommandoreal töötab edasi muutumatuna (samad CLI argumendid, sama käitumine).

**Implementatsioon:** `voog.py` muutub thin shim'iks mis impordib `voog_mcp.client`-ist API helper'eid + `voog_mcp.tools.*`-st funktsioone, koondab need praeguse `main()`-i taha. Mingit kasutaja-poolset muudatust ei nõua.

**Pikemaajaline plaan:** kui MCP usage on stabiilne ja CLI kasutus väike, võib v3-s kaaluda CLI'd amortiseerida `voog-mcp-cli` console script'i kasuks. Praegu mitte.

### 3.6. Konfiguratsioon: env vars

**Otsus:** MCP server loeb konfiguratsiooni keskkonnamuutujatest, NIME-spetsiifiliselt MCP-kontekstist:

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "voog-runnel": {
      "command": "voog-mcp",
      "env": {
        "VOOG_HOST": "runnel.ee",
        "VOOG_API_TOKEN": "d9b002620b07f4b3e796fed18bed8b29"
      }
    },
    "voog-stella": {
      "command": "voog-mcp",
      "env": {
        "VOOG_HOST": "stellasoomlais.com",
        "VOOG_API_TOKEN": "..."
      }
    }
  }
}
```

**CLI side:** `voog.py` jätkab `voog-site.json` cwd-st lugemist (backward-compat).

**MCP side:** ei kasuta `voog-site.json` ega `.env` walking'ut — ainult expicit env vars MCP server-spec'ist. See kõrvaldab cwd-ebausaldusväärsuse.

### 3.7. Strukteeritud väljund

**Otsus:** Tool'id tagastavad MCP `TextContent` JSON-iga *AND* fallback human-readable kokkuvõttega. Resource'id tagastavad puhast JSON-i (või template'i sisu raw-ina).

Näide `pages_list` tool'i vastus:
```python
return [
    TextContent(type="text", text=json.dumps(simplified_pages, indent=2)),
]
```

Erikorras (push, pull, snapshot) — kus operatsioon teeb palju asju — tool tagastab kokkuvõtte teksti + structured `meta` blockid.

## 4. Tool inventuur

Mapping olemas-olevatelt CLI käskudelt MCP tool'ide vahele.

| MCP Tool | CLI ekvivalent | Annotations | Kirjeldus |
|---|---|---|---|
| `pages_list` | `pages` | `readOnlyHint` | Listib kõik lehed |
| `page_get` | `page <id>` | `readOnlyHint` | Single page details |
| `pages_pull` | `pages-pull` | `readOnlyHint` | Save simplified pages.json |
| `pages_snapshot` | `pages-snapshot <dir>` | `readOnlyHint` | Backup pages + contents |
| `layout_rename` | `layout-rename <id> <title>` | `destructiveHint=false` | Rename layout, säilita id |
| `layouts_pull` | `pull` | `readOnlyHint` (output to disk) | Layout/asset sync alla |
| `layouts_push` | `push [files]` | `destructiveHint=true` | Layout/asset sync üles |
| `page_set_hidden` | `page-set-hidden <ids> true/false` | — | Bulk hidden flag toggle |
| `page_set_layout` | `page-set-layout <pid> <lid>` | — | Reassign layout |
| `page_delete` | `page-delete <id>` | `destructiveHint=true` | Kustuta leht |
| `products_list` | `products` | `readOnlyHint` | E-poe tooted |
| `product_get` | `product <id>` | `readOnlyHint` | Single toode |
| `product_update` | `product <id> <field> <value>` | — | Uuenda toote välja |
| `product_set_images` | `product-image <id> <files>` | — | Vaheta toote pildid |
| `redirects_list` | `redirects` | `readOnlyHint` | Kõik ümbersuunamised |
| `redirect_add` | `redirect-add <a> <b> [type]` | — | Lisa ümbersuunamine |

Tool'ide JSON Schema'd: vt implementatsiooniplaani Task 8–13.

## 5. Resource inventuur

| Resource URI | Kirjeldus | Tagastab |
|---|---|---|
| `voog://pages` | Lehekülgede struktuur | JSON list (sama mis `pages_list`) |
| `voog://pages/{id}` | Üksiku lehe info | JSON page object |
| `voog://pages/{id}/contents` | Lehe sisuplokid | JSON contents array |
| `voog://layouts` | Layoutide nimekiri | JSON list (id, title, content_type) |
| `voog://layouts/{id}` | Layout body | Raw `.tpl` content |
| `voog://articles` | Artiklite nimekiri | JSON list |
| `voog://articles/{id}` | Artikli body | HTML body content |
| `voog://products` | Toodete nimekiri | JSON list (sama mis `products_list`) |
| `voog://products/{id}` | Toote details | JSON product (sh translations + variants) |
| `voog://redirects` | Redirect rules | JSON list |

## 6. MCP server capabilities

Server avaldab init-handshake'is:

```python
ServerCapabilities(
    tools=ToolsCapability(listChanged=False),
    resources=ResourcesCapability(
        subscribe=False,
        listChanged=False,
    ),
    prompts=PromptsCapability(listChanged=False),  # v2
    logging=LoggingCapability(),
)
```

## 7. Vea-haldus

MCP-spetsiifiline error response:
- Validation errors → `INVALID_PARAMS` (-32602)
- API auth errors → `INVALID_REQUEST` (-32600) + struct'eeritud `data` field
- API server errors → `INTERNAL_ERROR` (-32603) + Voog response code data'sse

Iga tool wrap'ib oma logic'u try/except-iga ja tagastab `is_error=True` content blocks korrektsetel vea-stsenaariumitel (mitte raises).

## 8. Pikkade operatsioonide progress (v2)

`pages_snapshot` 85 lehega võtab 30–60s. MCP `notifications/progress`:

```python
async def pages_snapshot(ctx: Context, output_dir: str):
    pages = await client.api_get_all("/pages")
    for i, p in enumerate(pages):
        await ctx.report_progress(progress=i, total=len(pages))
        ...
```

**v1: sünkroonne, ilma progress'ita.** **v2: progress notifications.**

## 9. Distribution

`pyproject.toml`:
```toml
[project]
name = "voog-mcp"
version = "0.1.0"
dependencies = ["mcp>=0.9.0"]

[project.scripts]
voog-mcp = "voog_mcp.__main__:main"
voog = "voog_mcp.cli:main"  # backward-compat CLI
```

Install: `pip install -e .` (lokaalne dev) või `pip install voog-mcp` (PyPI hiljem).

## 10. Testimine

3 kihti:
1. **Unit testid** (`tests/test_client.py`, `tests/test_tools_*.py`): mocked HTTP, kontrollivad iga funktsiooni eraldi
2. **CLI regression** (`tests/test_voog.py`): olemas-olevad CLI testid jätkavad — backward-compat verifikatsioon
3. **MCP integration** (`tests/test_mcp_integration.py`): Anthropic'u [MCP inspector](https://github.com/modelcontextprotocol/inspector) abil — server initialize → list_tools → call_tool → assert response. Jookseb subprocess kaudu.

## 11. Phasing

| Faas | Sisu | Saidi tarbeks |
|---|---|---|
| **MVP (v0.1)** | Server skeleton + 17 tools + sync output | Stella + Runnel kasutusel |
| **v0.2** | Resources (passive read), tool annotations | Migratsiooni-tüüpi töödeks |
| **v0.3** | Progress notifications, prompts | Pikemajalised operatsioonid |
| **v1.0** | PyPI release, dokumentatsioon | Avalikuks kasutuseks |

## 12. Avatud küsimused

1. **MCP SDK versioon:** kontrolli `mcp` paketi viimast versiooni `pip install mcp`. Spec'i ajal eeldame `>=0.9.0`.
2. **Async/sync:** MCP SDK toetab mõlemat. Kuna olemas-olev voog.py on sync (urllib), MVP võiks jääda sync'iks. Kui async vaja progress'i jaoks, refaktoreeri v0.3-s.
3. **MCP transport:** stdio MVP-l, HTTP/SSE võimaldatud edaspidi remote'i jaoks (nt cloud-hostitud MCP).

## 13. Viited

- [MCP Specification](https://modelcontextprotocol.io/specification)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
- [Voog Admin API](https://www.voog.com/developers/api)
- [Existing voog skill](`~/.claude/skills/voog/SKILL.md`) — Voog API gotchas, render modes, object types
