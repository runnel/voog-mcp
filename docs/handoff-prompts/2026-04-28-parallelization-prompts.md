# voog-mcp parallelization — sessiooniprompid

Lühikesed bootstrap'id. Iga prompt loeb spetsist oma faasi ja täidab selle. Pasten need eraldi sessioonidesse.

**Spec:** [`docs/specs/2026-04-28-parallelization.md`](../specs/2026-04-28-parallelization.md)

## Soovitatud järjekord

1. **Faas 1 esimesena** — annab `_concurrency.py` helper'i, mida kõik teised vajavad
2. **Faas 2 järgmisena** — kõige suurema mõjuga (site_snapshot)
3. **Faas 6 enne Faas 3–5** — kui `?include=` list-endpoint'il toetab, mõjutab Faas 2 implementatsiooni
4. **Faasid 3, 4, 5 paralleelselt** — eraldi sessioonidesse, ei sõltu üksteisest

| # | Faas | Sõltuvused | Hinnang |
|---|---|---|---|
| 1 | `_concurrency.py` infra | — | 30 min |
| 2 | `site_snapshot` | F1 | 60 min |
| 3 | `layouts_pull` | F1 | 30 min |
| 4 | `product_set_images` | F1 | 45 min |
| 5 | `page_set_hidden` + `layouts_push` | F1 | 45 min |
| 6 | `?include=` verifitseerimine | F1, F2 | 30 min (verify) + 30 min (apply, kui toetab) |

---

## Sessioon 1 — `_concurrency.py` infra

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 1: _concurrency.py infra + testid" ja täida ülesanne lõpuni — kuni PR on loodud.

Branch: refactor/parallelize-concurrency-helper
PR base: main
PR title: "feat(mcp): parallel_map helper for tool-level concurrency"
PR body: viita spec'i § 4.2 + § 5 Faas 1.

Järgi spec'i § 4 arhitektuurseid otsuseid. Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Sessioon 2 — `site_snapshot` parallelization

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 2: site_snapshot (kõrgeim mõju)" ja täida ülesanne lõpuni — kuni PR on loodud.

Eeldus: Faas 1 (parallel_map helper) on juba merged main'i. Kui pole, peatu ja anna teada.

Branch: refactor/parallelize-site-snapshot
PR base: main
PR title: "perf(mcp): parallelize site_snapshot list + per-resource fetches"
PR body: viita spec'i § 2 (oodatav 5–10× speedup) + § 5 Faas 2.

Verifitseerimine: aja RUN_SMOKE=1 .venv/bin/python -m unittest tests.test_mcp_integration -v ja kontrolli, et site_snapshot kestab vähemalt 3× kiiremini kui enne (vajalik RUNNEL_VOOG_API_KEY Claude/.env-is).

Järgi spec'i § 4.5 partial failure semantikat — `skipped` array säilib. Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Sessioon 3 — `layouts_pull` parallelization

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 3: layouts_pull" ja täida ülesanne lõpuni — kuni PR on loodud.

Eeldus: Faas 1 (parallel_map helper) on juba merged main'i.

Branch: refactor/parallelize-layouts-pull
PR base: main
PR title: "perf(mcp): parallelize layouts_pull per-layout detail fetches"
PR body: viita spec'i § 5 Faas 3.

Sequential .tpl write loop'i ÄRA paralleliseeri — fetch'id paralleelselt, write'id sequential. Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Sessioon 4 — `product_set_images` collect-then-decide

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 4: product_set_images (collect-then-decide)" ja täida ülesanne lõpuni — kuni PR on loodud.

Eeldus: Faas 1 (parallel_map helper) on juba merged main'i.

Branch: refactor/parallelize-product-images
PR base: main
PR title: "perf(mcp): parallelize product_set_images uploads (collect-then-decide)"
PR body: viita spec'i § 4.6 (orphan asset semantika muutus) + § 5 Faas 4.

OLULINE: säilita "any failure → don't PUT product" invariant. Erinevus: orphan upload'eid võib olla rohkem kui first-failure-break-il. Dokumenteeri see error message'is. Olemasolev test, mis kontrollib first-failure-aborts-rest, tuleb asendada — uus invariant on "any failure prevents product PUT". Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Sessioon 5 — `page_set_hidden` + `layouts_push`

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 5: page_set_hidden + layouts_push (writes, max_workers=4)" ja täida ülesanne lõpuni — kuni PR on loodud.

Eeldus: Faas 1 (parallel_map helper) on juba merged main'i.

Branch: refactor/parallelize-bulk-writes
PR base: main
PR title: "perf(mcp): parallelize page_set_hidden + layouts_push bulk writes"
PR body: viita spec'i § 4.3 (max_workers=4 writes) + § 4.4 (rate limit etikett) + § 5 Faas 5.

Vaikimisi max_workers=4 (mitte 8 nagu read'id) — kirjutamised on tundlikumad. Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Sessioon 6 — `?include=` verifitseerimine + apply

```
Loe /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/docs/specs/2026-04-28-parallelization.md sektsioon "Faas 6: ?include= list endpoint'il (verifitseeritav)" ja täida ülesanne kahe sammuna.

Eeldus: Faas 1 (parallel_map helper) JA Faas 2 (site_snapshot parallelization) on juba merged main'i. Faas 6 muudab Faas 2-s tehtud per-product detail loop'i — peab Faas 2 valmis olema enne, muidu redigeerid faili, mida pole veel olemas. Kui Faas 2 pole merged, peatu ja anna teada.

SAMM 1 (verifitseerimine):
- Käivita käsitsi curl kõne või Python REPL'is:
  https://runnel.ee/admin/api/ecommerce/v1/products?include=variant_types,translations
- Kontrolli vastuse esimest objekti — kas `variant_types` on täis-objektid (mitte ainult ID'd)?
- Sama kontroll: /admin/api/articles?include=body — kas `body` field on list-vastuses olemas?
- Kasuta API token'it Claude/.env'st (RUNNEL_VOOG_API_KEY).
- Tee leiud teatavaks ja KÜSI kasutajalt enne SAMM 2-le minekut.

SAMM 2 (kui toetab — apply):
- Eemalda site_snapshot per-product detail loop, asenda ?include= parameetriga list-päringule.
- Sama articles peal kui body=list toetab.
- Säilita /products/{id} per-product GET ainult product_get tool'i jaoks (mitte snapshot'is).

Branch: refactor/include-on-list-endpoints (kui apply'd)
PR base: main
PR title: "perf(mcp): drop per-product detail loop in site_snapshot — list ?include= covers it"
PR body: viita spec'i § 5 Faas 6 + lisada vere'st curl-tulemused, et kontrakt fixitud.

Skill: /Users/runnel/.claude/skills/voog/SKILL.md
```

---

## Pärast kõikide merge'i

Lokaalselt:

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
git checkout main && git pull
.venv/bin/python -m unittest discover tests
RUN_SMOKE=1 .venv/bin/python -m unittest tests.test_mcp_integration -v

# Käsitsi mõõdupuu — vali sait
time RUN_SMOKE=1 .venv/bin/python -c "
from voog_mcp.client import VoogClient
from voog_mcp.tools.snapshot import _site_snapshot
import os
client = VoogClient(host='runnel.ee', api_token=os.environ['RUNNEL_VOOG_API_KEY'])
result = _site_snapshot({'output_dir': '/tmp/voog-snap-bench'}, client)
print(result)
"
```

Vahetult enne ja vahetult pärast vahetatakse parallelization-PR-id ja võrreldakse `time`-i. Spec ennustab 5–10× kiirenemist site_snapshot peal.
