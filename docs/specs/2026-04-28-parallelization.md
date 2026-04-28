# voog-mcp: Parallelization (Spec + Plan)

**Status:** spec, ootab implementatsiooni
**Spec kuupäev:** 2026-04-28
**Eelnev:** [2026-04-26-mcp-server.md](2026-04-26-mcp-server.md), review followups (PR #39–#46), simplify pass (PR #47)
**Repo:** https://github.com/runnel/voog-mcp

## 1. Eesmärk

`/simplify` faas tuvastas, et pärast PR #44-t (sync `def call_tool` + `asyncio.to_thread` dispatch) on tee lahti sisemiseks paralleliseerimiseks — aga praegu fetch'b iga tool oma loop'is jada-järgi. Suurim valupunkt: `site_snapshot` keskmise saidi peal teeb 130+ jada-päringut ja võtab 30–60s. Selle faasi eesmärk on vähendada wallclock'i 5–10× ilma servlerituuri ümber kirjutamata.

**Mitte-eesmärk:** async refactor (tools jäävad sync def, dispatch kasutab juba `asyncio.to_thread`), server-wide rate limit'er, retry/backoff (eraldi faas, kui vaja).

## 2. Mõju

Mõõdupuu — hetkeseis vs. oodatav:

| Operatsioon | Praegu | Pärast | Eeldus |
|---|---|---|---|
| `site_snapshot` (50 lehte + 50 artiklit + 30 toodet) | ~30–60s | ~5–10s | 16 list endpoint'i + per-resource fetch'id paralleelselt, max_workers=8 |
| `layouts_pull` (20 layoutit) | ~5–8s | ~1–2s | per-layout detail fetch'id paralleelselt |
| `product_set_images` (4 pilti × 2.5 MB) | ~6–10s | ~2–3s | upload'id paralleelselt, max_workers=3 |
| `page_set_hidden` (20 lehte) | ~4–6s | ~1–2s | bulk PUT'id paralleelselt, max_workers=4 |
| `layouts_push` (20 layoutit) | ~4–6s | ~1–2s | bulk PUT'id paralleelselt, max_workers=4 |

Numbrid eeldavad keskmist 200–400ms latency'it Voog API peale. Tegelikud arvud sõltuvad saidi suurusest ja võrgu kvaliteedist.

## 3. Skoop

**Implementeeritav:**

1. `voog_mcp/_concurrency.py` — uus moodul, sisaldab `parallel_map(fn, items, *, max_workers)` helper'it
2. `voog_mcp/tools/snapshot.py` — `_site_snapshot` paralleliseerimine
3. `voog_mcp/tools/layouts_sync.py` — `_layouts_pull` per-layout fetch'id; `_layouts_push` bulk PUT'id
4. `voog_mcp/tools/products_images.py` — upload'id paralleelselt, **collect-then-decide** semantikaga
5. `voog_mcp/tools/pages_mutate.py` — `_page_set_hidden` bulk PUT'id

**Verifitseerimist nõudev (Faas 6):**

6. Voog `/products?include=variant_types,translations` list endpoint'il — kui Voog tagastab täisinfot list-vastuses, saame `_site_snapshot`-ist eemaldada per-product detail loop'i (~30 round trip'i kustub). Vajab API doku või katselist tuvastust.

## 4. Arhitektuursed otsused

### 4.1. Sync def + ThreadPoolExecutor (mitte async refactor)

**Otsus:** Tool functions jäävad sync `def` (PR #44 contract). Paralleliseerimine toimub *funktsiooni sees* `concurrent.futures.ThreadPoolExecutor` kaudu.

**Põhjendus:**
- `server.handle_call_tool` wrap'b iga tool kõnet `asyncio.to_thread`-iga — üks outer thread per tool call
- Tool sees `ThreadPoolExecutor(max_workers=N)` spawn'b worker thread'e, urllib HTTP päringud jooksevad neis paralleelselt
- Outer thread (asyncio.to_thread) blokeerub `executor.shutdown(wait=True)`-l, MCP event loop on jätkuvalt vaba
- Async refactor (`await asyncio.gather(...)`) nõuaks tool layer'i ümber kirjutamist sync→async — suur diff, vähe kasu

**Kompromiss:** kahekordne thread pool (asyncio.to_thread + ThreadPoolExecutor) on natuke ülesehitus, aga thread'id on cheap (≤10 per tool call) ja Python GIL ei häiri kuna I/O-bound.

### 4.2. Üks shared helper, mitte iga tool oma loop

**Otsus:** `voog_mcp/_concurrency.py` exporting `parallel_map`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    fn: Callable[[T], R],
    items: list[T],
    *,
    max_workers: int = 8,
) -> list[tuple[T, R | None, Exception | None]]:
    """Run ``fn(item)`` in parallel across ``items``, return per-item result.

    Returns a list of ``(item, result, exception)`` tuples in the original
    input order. Exactly one of ``result`` / ``exception`` is non-None per
    tuple. Caller decides what to do with errors — never raises.

    Order: results are reordered to match ``items`` (not completion order),
    so callers can pair input/output deterministically without bookkeeping.

    Empty ``items`` returns ``[]`` without spawning a pool.
    """
    if not items:
        return []
    results: list[tuple[T, R | None, Exception | None]] = [None] * len(items)  # type: ignore
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fn, item): (idx, item)
            for idx, item in enumerate(items)
        }
        for future in as_completed(futures):
            idx, item = futures[future]
            try:
                results[idx] = (item, future.result(), None)
            except Exception as e:
                results[idx] = (item, None, e)
    return results
```

**Põhjendus:**
- Identne paralleelsuse muster ~5 tool'is — copy-paste oleks ennustatav drift
- `(item, result, exception)` tuple-vorm ühildub olemasolevate per-id breakdown'idega (`page_set_hidden`-i `results: [{ok, error}]`)
- Sync kontrakt — ei nõua call_tool muutust async-iks
- `as_completed` kuid sisemine `idx`-järjestus säilitab caller'ile deterministliku vastuste järjekorra

### 4.3. max_workers vaikimisi

| Operatsiooni tüüp | Default | Põhjendus |
|---|---|---|
| Read-only fetch (snapshot list endpoint'id, layouts detail) | 8 | Voog API ei dokumenteeri rate limit'i — 8 paralleelset GET'i on enamiku SaaS API'de mugavustsoonis |
| Write PUT/POST (page_set_hidden, layouts_push) | 4 | Kirjutamised on tundlikumad rate limit'ile + tagasipööramine on raskem; konservatiivsem |
| Asset upload (S3 presigned URL) | 3 | Iga upload on multi-MB binary; 3 paralleelset 5 MB upload'i ≤ 15 MB net I/O |

Iga tool võib oma valida — helper võtab `max_workers=...` kwarg'iks. Vaikimisi ei ole konfigureeritav env var'iga praegu (lisada saab kui vaja).

### 4.4. Rate limit etikett

Voog API rate limit'i ei dokumenteeri; meie konservatiivsed vaikimisi väärtused (8 read / 4 write) on alla tavalise SaaS API "20 RPS per token" piiri. Kui mingi tool 429 vastab, jääb see helper'i `(item, None, exception)` tuple'is kinni — caller näeb selgelt, mis ebaõnnestus.

**Mitte-skoop:** automaatne retry/backoff. Kui caller näeb 429-sid, jookseta uuesti või vähenda max_workers'it käsitsi. Retry-logic'u lisab eraldi faas, kui see osutub vajalikuks.

### 4.5. Partial failure semantika

Iga tool säilitab oma praeguse error-shape'i:

| Tool | Praegune key | Säilib |
|---|---|---|
| `page_set_hidden` | `results: [{id, ok, error?}]` | ✅ |
| `layouts_pull` | `per_layout_errors: [{layout_id, error}]` | ✅ |
| `layouts_push` | `results: [{file, ok, id?, error?}]` | ✅ |
| `pages_snapshot` | `per_page_errors: [{page_id, error}]` | ✅ |
| `site_snapshot` | `skipped: [{file, reason}]` | ✅ |
| `product_set_images` | `failed: [{filename, error}]` + `uploaded: [...]` | ✅ |

Helper-il pole oma error-shape'i — ta ainult tagastab tuple'id, tool projitseerib need oma sõnastikku.

### 4.6. `product_set_images` collect-then-decide

Praegu loop break'b first-failure peale. Paralleelses versioonis EI saa break — kõik upload'id käivad korraga. Selle juures tuleb käsitsi tagada sama "any failure → don't PUT product" invariant:

```python
results = parallel_map(_upload_asset, paths, max_workers=3)
uploaded = [{"filename": p.name, ...} for (p, asset, _) in results if asset]
failed = [{"filename": p.name, "error": str(e)} for (p, _, e) in results if e]

if failed:
    return error_response(...)  # SAMA TEEN KUI PRAEGU
```

Säilib: surface'b orphan upload'id `uploaded`-s, ei tee product PUT'i. Erinevus: nüüd kõik 4 upload'i on tehtud (kui 1 ebaõnnestub, 3 muud on Voog'is library's), enne — ainult need, mis enne failure'it järjekorras tulid. Net mõju: orphan'eid võib olla rohkem. **Aktsepteeritav** — orphan asset'id on inertselt asset library's, kasutaja saab need re-link'ida või kustutada admin UI's.

## 5. Faasid

Iga faas = üks PR ühest sessioonist, branch `refactor/parallelize-<name>`.

### Faas 1: `_concurrency.py` infra + testid

**File:** `voog_mcp/_concurrency.py` (uus, ~40 rida koos docstring'iga)
**Testid:** `tests/test_concurrency.py` (uus)

Testitsenaarium:
- empty list → `[]` (ei spawn'i pool'i)
- single item → 1-element tuple list, õige order
- multiple items → kõik fn'i kutsumised toimusid, results indeksi-järjestuses
- one item raises → vastav tuple on `(item, None, exception)`, teised on edukalt tagastatud
- preserve input order: items=[3,1,4,1,5,9], fn=lambda x: time.sleep(random)*x — output items järjekord identne sisendiga (mitte completion order)
- max_workers=1 → võrdne sequential map'iga (sanity check)

**Acceptance:** kõik testid läbivad, helper kasutamiseks valmis.

### Faas 2: `site_snapshot` (kõrgeim mõju)

**File:** `voog_mcp/tools/snapshot.py` `_site_snapshot`

Praegu ~5 sequential loop'i:
1. `SITE_SNAPSHOT_LIST_ENDPOINTS` (16 endpoint'i)
2. `SITE_SNAPSHOT_SINGLETONS` (2 endpoint'i — jätta sekventsiaalseks, väike võit)
3. per-page contents
4. per-article details
5. per-product details

Kõik loop'id 1, 3, 4, 5 → `parallel_map`. Iga loop oma `parallel_map` kõne (mitte üks suur — sõltuvused: per-page contents vajab pages_data juba olemas).

**Vahesammud (säilita):**
- `pages_data`, `articles_data`, `products_data` listide täitmine pärast list endpoint'i parallelliseerimist (samades for-loop'ides nagu praegu, aga results'st reading)
- per-resource detail fetch'id eraldi `parallel_map` kõnedena
- `skipped` array kogub kõik vead ühte kohta

**Testid:**
- olemasolevad `tests/test_tools_snapshot.py` peavad jätkama läbima (mock Voog API, kontrolli kirjutatud failide arv)
- lisa test: parallel_map paralleliseerib (mock parallel_map, verify call args + max_workers)
- lisa test: failure ühest endpoint'ist ei aborti teisi (skipped'is on 1 kirje, ülejäänud failid kirjutatud)

**Acceptance:** site_snapshot wallclock vähemalt 3× kiirem keskmise saidi peal (käsitsi mõõdetav RUN_SMOKE'iga). Test suite roheline.

### Faas 3: `layouts_pull`

**File:** `voog_mcp/tools/layouts_sync.py` `_layouts_pull`

Praegu loop:
```python
for layout in layouts:
    detail = client.get(f"/layouts/{lid}")  # ← jadas
    ...write_text(...)
```

Refactor:
1. Esmalt `parallel_map(client.get, [f"/layouts/{lid}" for lid in valid_layout_ids], max_workers=8)` — fetch'b kõik detailid paralleelselt
2. Seejärel sequential loop, mis kirjutab .tpl faile + ehitab manifest'i (sync I/O ainult, fast — mitte vaja paralleliseerida)

**Testid:**
- olemasolevad `tests/test_tools_layouts_sync.py` testid läbivad
- lisa test: parallel_map kasutus, max_workers=8
- lisa test: failure ühest layout detail fetch'ist ei aborti teisi (per_layout_errors'is 1 kirje, teised kirjutatud)

**Acceptance:** wallclock vähemalt 3× kiirem 20-layoutilise saidi peal.

### Faas 4: `product_set_images` (collect-then-decide)

**File:** `voog_mcp/tools/products_images.py` `_product_set_images`

Praegu loop break'b first-failure peale:
```python
for path in paths:
    try:
        asset = _upload_asset(path, client)
    except Exception as e:
        failed.append(...)
        break  # ← stop on first failure
```

Refactor: kõik upload'id paralleelselt (max_workers=3), KÕIK kogutakse enne otsustamist:
```python
results = parallel_map(lambda p: _upload_asset(p, client), paths, max_workers=3)
uploaded, failed = [], []
for path, asset, exc in results:
    if exc:
        failed.append({"filename": path.name, "error": str(exc)})
    else:
        uploaded.append({"filename": path.name, ...})
# Kui failed pole tühi → ei tee product PUT'i (sama nagu praegu)
```

**Erinevus praeguse semantikaga:** orphan upload'eid võib olla rohkem (kui 1 fail'b 4-st, jääb 3 orphan'i, mitte 0–3). Aktsepteeritav — assetid on inertne library's. Dokumenteeri see error_message'i.

**Testid:**
- olemasolevad `tests/test_tools_products_images.py` testid (peavad uuenemma — break-mehhanism on muutunud)
- ÜLE VAATA: test, mis kontrollib "first failure aborts rest" — see invariant muutub. Asenda: "any failure prevents product PUT" (jääb sama).
- lisa test: 2-of-4 upload'i fail → 2 uploaded + 2 failed surface'b, ei tehta product PUT'i

**Acceptance:** wallclock 4-pildilise toote peal vähemalt 2× kiirem.

### Faas 5: `page_set_hidden` + `layouts_push` (writes, max_workers=4)

**Files:**
- `voog_mcp/tools/pages_mutate.py` `_page_set_hidden`
- `voog_mcp/tools/layouts_sync.py` `_layouts_push`

Mõlemad on bulk-PUT operations. Sama muster:

```python
def _put_one(item):
    client.put(f"/pages/{item['id']}", {"hidden": item["hidden"]})

results = parallel_map(_put_one, items, max_workers=4)
# Project results back to {id, ok, error} shape
```

**Hoiatus:** kuigi PUT'id on pages/layouts'i eri ID'idele, võib Voog tree-derived cache'idel olla eventual consistency. Praktikas — pages-set-hidden pärast 20 PUT'i jäab jätkuvalt ühe sec sees nähtavaks; me ei lisa explicit-wait'i.

**Testid:**
- olemasolevad testid läbivad
- lisa test: parallel_map kasutamine + max_workers=4
- lisa test: 1-of-N PUT fail'b → tagastatud results säilitavad eri kirje, teised olid OK

**Acceptance:** wallclock 20-page bulk-set'i peal vähemalt 3× kiirem.

### Faas 6: `?include=` list endpoint'il (verifitseeritav)

**Eeldus:** kui `/products?include=variant_types,translations` toob list-päringus täisinfo, saab `_site_snapshot` per-product detail loop'i kustutada (~30 round trip'i kaob).

**Sammud:**
1. **Verifitseeri esimese asjana** — käsitsi curl kõne või RUN_SMOKE test'iga: kas `/products?include=variant_types,translations` list-vastus sisaldab `variant_types`-i objektidena, mitte ainult `variant_type_ids`-na? Kontrolli ka pages, articles: kas `?include=contents` vms olemas?
2. Kui jah — eemalda `_site_snapshot` per-product detail loop, asenda `?include=` parameetriga list-päringule
3. Sama ülevaade pages/articles puhul: kas `/articles?include=body` annab täisbody list-vastuses? Kui jah, drop per-article detail loop ka.

**Skoop:** ainult site_snapshot — `voog://products` resource'is `product_get` ja `product_update` jäävad detail-päringule (tugev invariant: detail = täisinfo, list = lihtsustatud).

**Testid:** verify-first; kui ?include= ei tööta list peal, faas peatub. Kui töötab, kustuta vana loop + lisa test, et ainsa list-päringu tulemus on identne (kahe-päringu kombo'ga).

**Acceptance:** site_snapshot'st kaob N round trip'i (kus N = products + articles + pages contents tarvis), iga sit-snapshot lendab kiiremini ka pärast Faas 2-i optimisationi.

## 6. Test strateegia

**Unit-testid:** mock `parallel_map`-i sees kasutatav `fn` callable. Kontrolli call-args ja vastuste järjestust. **Ära mock'i `ThreadPoolExecutor`-it** — ta on stdlib stable, mock'imine maksab rohkem kui väärt.

**Integratsioonitestid:** `RUN_SMOKE=1 site_snapshot` enne ja pärast — käsitsi `time` kõrvuti, oodata vähemalt 3× kiirenemine 50+ ressursiga saidil.

**Regressioonitestid:** olemasolevad `tests/test_tools_*.py` (438+ testi) peavad jätkama läbima ilma muutmata — paralleliseerimine on impl detail, mitte API muudatus.

**Concurrency edge cases:**
- `parallel_map(fn, [], max_workers=8)` → tühi list (no pool spawn)
- `parallel_map(fn, [x], max_workers=8)` → 1-element tuple list (mitte hangib)
- mocked client kõik kõned raise'vad → kõik `(item, None, exc)` tuples'is, helper ei reraise

## 7. Mitte-skoop

- **Async refactor**: tools jäävad sync def. Kui v0.3 progress notifications nõuavad async, see on eraldi spec.
- **Server-wide rate limiter**: praegu max_workers per tool — globaalset thread-budget'i ei ole. Kui ühe Claude sessiooni kõrval töötab teine MCP klient samaaegselt, võime kokku sõita Voog rate limit'iga. Aktsepteeritav — single-user MCP.
- **Retry/backoff**: 429/5xx response'd jäävad helper-tuple'isse `exception`-na. Kui osutub vajalikuks (rangelt empiiriline), lisab eraldi faas wrap'i `_request`-i ümber.
- **`get_all` async streaming**: praegu accumulate'b kõik leheküljed listi. Generator-versiooni `iter_all()` võiks v0.3 lisada — see spec ei adresseeri.

## 8. Faaside järjekord ja sõltuvused

Kõik 6 faasi on iseseisvad pärast Faas 1-i (`_concurrency.py`). Soovitatav järjekord:

1. **Faas 1** kohustuslik enne kõiki teisi (lisab helper'i, mida kõik kasutavad)
2. **Faas 2** (site_snapshot) — kõrgeim mõju, kõige väärt teha esimese pärast infra
3. **Faas 6** (verify ?include=) — alles peale Faas 2-i, sest mõjutab snapshot'i implementatsiooni; kui ?include= toetatud, võtab see osa snapshot-ist täiesti välja
4. **Faasid 3, 4, 5** paralleelselt (eraldi sessioonidesse) — ei sõltu üksteisest

Ühe sessiooni eraldi promptid: vt `docs/handoff-prompts/2026-04-28-parallelization-prompts.md` (loodavas).

## 9. Avatud küsimused

1. **Voog rate limit?** — ametlikku numbrit ei dokumenteerita. Konservatiivne start (8 read / 4 write / 3 upload), kohanda kui 429-d tulevad.
2. **`?include=` list-endpoint'il?** — verify Faas 6-s. Kui toetab, märkimisväärne lisavõit.
3. **`asyncio.gather` versus ThreadPoolExecutor?** — gather nõuaks tools sync→async, mille me PR #44-s tahtlikult vältisime. Jää ThreadPoolExecutor'iga.

## 10. Viited

- Eelnev MCP server spec: [2026-04-26-mcp-server.md](2026-04-26-mcp-server.md)
- Review followup'id: PR #39, #40, #41, #42, #43, #44, #45, #46
- Simplify pass: PR #47
