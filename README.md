# voog-mcp

Voog CMS automatiseerimise tööriistad — kasutusel mitme Voog saidi haldamisel (Stella Soomlais, Tõnu Runnel jt).

## voog.py

Single-file Python CLI Voog Admin API + Ecommerce v1 API jaoks. Pull/push template'id, hallata products, redirects, pages.

```bash
cd ~/path/to/voog-site-repo  # peab sisaldama voog-site.json'i
python3 ~/path/to/voog.py help
```

Vajab:
- Python 3 (stdlib only — urllib, json, pathlib)
- `.env` fail koos API key'dega (otsib `.env` voog.py kõrvalt, töökaustast, parent'idest)
- `voog-site.json` igal saidikaustal: `{"host": "...", "api_key_env": "..."}` (turvameede saitide segiajamise vastu)

Vt voog.py docstring'i kõigi käskude jaoks.

## Tests

```bash
cd ~/path/to/voog-mcp
python3 -m unittest discover tests -v
```

Testid kasutavad stdlib `unittest` + `unittest.mock` (no pytest dependency).

## Skill

Voog'i tehniliste detailide kohta (API endpoints, render modes, page/article/element comparison) — vt `~/.claude/skills/voog/SKILL.md`.
