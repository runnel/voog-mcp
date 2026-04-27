#!/usr/bin/env python3
"""
Voog Kit — lihtne asendus voog-kit tööriistale.
Kasutus:
  python3 voog.py pull                          # laeb kõik mallifailid alla
  python3 voog.py push <fail> [fail2] [...]     # laeb faili(d) üles
  python3 voog.py push                          # laeb KÕIK failid üles (küsib kinnitust)
  python3 voog.py list                          # näitab kõiki faile

  python3 voog.py serve [--port 8080]           # lokaalne proxy server (JS/CSS testimiseks)
      Proxib live-saidi HTML-i, asendab JS/CSS failid kohalike versioonidega.
      Ava http://localhost:8080 ja muuda kohalikke faile — refresh näitab muudatusi.

  python3 voog.py products                      # kõik tooted (id, nimi, slug)
  python3 voog.py product <id>                  # ühe toote täisinfo koos tõlgetega
  python3 voog.py product <id> <väli> <väärtus> # uuenda toote välja
      Väljad: name-et, name-en, slug-et, slug-en
      Näide:  python3 voog.py product 2961057 name-en "Trippelgänger"
      Mitu korraga: python3 voog.py product 2961057 name-et "Trippelgänger" name-en "Trippelgänger" slug-et "trippelganger" slug-en "trippelganger"

  python3 voog.py product-image <id> <fail> [fail2 ...]  # vahetab toote pildid
      Näide:  python3 voog.py product-image 3077700 kott.jpg
      Mitu pilti: python3 voog.py product-image 3077700 pilt1.jpg pilt2.jpg
      Esimene pilt = põhipilt (tootelistingus), ülejäänud = galerii

  python3 voog.py pages                         # kõik lehed (id, path, title, hidden, layout)
  python3 voog.py page <id>                     # ühe lehe täisinfo
  python3 voog.py pages-snapshot <dir>          # backup kõik lehed + contents JSON-i
  python3 voog.py site-snapshot <dir>           # comprehensive read-only backup of EVERY mutable resource
      Pages, articles, layouts, layout_assets, languages, redirect_rules, nodes,
      texts, content_partials, tags, forms, media_sets, assets, webhooks, elements,
      element_definitions, site, me, products (with variants+translations), per-page
      contents, per-article details, per-product details, sample rendered HTML
      (captures `<style data-voog-style>` block — the only place saved VoogStyle
      customizations are visible). 404s on optional endpoints are skipped, not fatal.
      REQUIRED pre-flight before layout-rename, mass push, layout swap, VoogStyle push.
      Refuses to overwrite existing dir.
  python3 voog.py layout-rename <id> <uus>      # nimeta layout ümber, säilita id
  python3 voog.py asset-replace <id> <uus-filename>  # asenda layout_asset uue id-ga (DELETE+POST workaround filename rename'iks)
      Voog API ei luba PUT-iga `filename` muuta — see käsk loob POST'iga uue
      asseti uue ID-ga ja jätab vana alles. Pärast template'ide uuendust ja
      pushimist tuleb vana asset MANUAALSELT DELETE'ida (käsk prindib curl-i).
  python3 voog.py layout-create component components/site-header.tpl
                                                # POST uus component
  python3 voog.py layout-create layout 'layouts/Front page.tpl'
                                                # POST uus layout
  python3 voog.py page-set-hidden <id>... true|false  # bulk hidden toggle
  python3 voog.py page-set-layout <page-id> <layout-id>  # reassign layout
  python3 voog.py page-delete <id> [--force]    # kustuta leht (küsib kinnitust)
  python3 voog.py pages-pull                    # salvesta pages.json (struktuur, ei sisaldu sisu)

  python3 voog.py redirects                     # kõik ümbersuunamised
  python3 voog.py redirect-add <allikas> <siht> [301|302|307|410]  # lisa ümbersuunamine
      Näide: python3 voog.py redirect-add /en/products/vana /en/products/uus 301

Seadistus:
  - .env fail (Claude/.env) sisaldab API võtmeid (nt VOOG_API_KEY, RUNNEL_VOOG_API_KEY)
  - Iga saidikaust peab sisaldama voog-site.json faili kujul:
      {"host": "stellasoomlais.com", "api_key_env": "VOOG_API_KEY"}
  - voog.py loeb voog-site.json praegusest töökaustast (cwd) ja keeldub
    töötamast kui see fail puudub. See on turvameede saitide segiajamise vältimiseks.
"""

import sys
import json
import re
import ssl
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from voog_mcp.client import VoogClient

# --- Seadistus ---

def load_env():
    """Otsi .env voog.py kõrvalt, siis töökaustast, siis parent'idest üles."""
    env = {}
    candidates = [Path(__file__).resolve().parent / ".env", Path.cwd() / ".env"]
    p = Path.cwd().resolve()
    for _ in range(6):  # otsime kuni 6 taset üles
        candidates.append(p / ".env")
        if p.parent == p:
            break
        p = p.parent
    seen = set()
    for env_path in candidates:
        env_path = env_path.resolve()
        if env_path in seen or not env_path.exists():
            continue
        seen.add(env_path)
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
        break  # esimese leitud .env piisab
    return env

ENV = load_env()

# Per-saidi konfiguratsioon (turvameede saitide segiajamise vältimiseks).
# Vajalik fail: voog-site.json praeguses töökaustas (cwd), formaat:
#   {"host": "stellasoomlais.com", "api_key_env": "VOOG_API_KEY"}
# voog.py keeldub töötamast kui voog-site.json puudub — see hoiab ära olukorra,
# kus Runneli kaustast jooksutatud käsk satuks Stella saidile (või vastupidi).
def load_site_config():
    config_path = Path.cwd() / "voog-site.json"
    if not config_path.exists():
        sys.stderr.write(
            f"❌ voog-site.json puudub praeguses kaustas: {Path.cwd()}\n"
            "   See fail tagab, et voog.py ei aja saiti segamini (nt Stella vs Runnel).\n"
            "   Liigu õigesse saidikausta (nt cd Claude/stellasoomlais-voog) või loo fail kujul:\n"
            '   {"host": "<domeen>", "api_key_env": "<env-muutuja-nimi>"}\n'
        )
        sys.exit(1)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if not cfg.get("host") or not cfg.get("api_key_env"):
        sys.stderr.write("❌ voog-site.json peab sisaldama 'host' ja 'api_key_env' välju.\n")
        sys.exit(1)
    return cfg

# --- Globals (lazy initialized by init_site()) ---
SITE_CONFIG = None
HOST = ""
API_KEY = ""
BASE_URL = ""
ECOMMERCE_URL = ""
HEADERS = {}
_client = None  # type: VoogClient | None

_HELP_CMDS = {"help", "-h", "--help"}


def init_site():
    """Lazy-load site config + create VoogClient. Idempotent."""
    global SITE_CONFIG, HOST, API_KEY, BASE_URL, ECOMMERCE_URL, _client
    if _client is not None:
        return  # already initialized
    SITE_CONFIG = load_site_config()
    HOST = SITE_CONFIG["host"]
    API_KEY = ENV.get(SITE_CONFIG["api_key_env"], "")
    if not API_KEY:
        sys.stderr.write(
            f"❌ Env muutuja '{SITE_CONFIG['api_key_env']}' puudub Claude/.env failist.\n"
            f"   voog-site.json viitab sellele, aga väärtus on tühi või puudub.\n"
        )
        sys.exit(1)
    BASE_URL = f"https://{HOST}/admin/api"
    ECOMMERCE_URL = f"https://{HOST}/admin/api/ecommerce/v1"
    _client = VoogClient(host=HOST, api_token=API_KEY)
    # HEADERS is mutated in place (update) — callers hold a reference to the
    # same dict object, so we must not rebind the name.
    HEADERS.update(_client.headers)

# Kohalikud failid — alati TÖÖKAUSTAS (cwd), mitte voog.py asukohas.
# See tagab, et iga saidikaust haldab oma faile eraldi.
LOCAL_DIR = Path.cwd()

ASSET_TYPE_TO_FOLDER = {
    "stylesheet": "stylesheets",
    "javascript": "javascripts",
    "image": "images",
    "font": "assets",
    "unknown": "assets",
}

# --- API abifunktsioonid ---
# Module-level wrappers — delegate to _client (VoogClient). Module-level
# functions preserved for backward-compat with existing tests + callers.

def api_get(path, params=None, base=None):
    return _client.get(path, base=base, params=params)

def api_put(path, data=None, base=None):
    return _client.put(path, data, base=base)

def api_post(path, data, base=None):
    return _client.post(path, data, base=base)

def api_delete(path, base=None):
    return _client.delete(path, base=base)

def api_get_all(path, base=None):
    """Laeb kõik lehed (pagination)."""
    return _client.get_all(path, base=base)

# --- Pull ---

def pull():
    print(f"Ühendun: {HOST}")
    LOCAL_DIR.mkdir(exist_ok=True)

    # 1. Layoutid (mallid)
    print("\n📄 Laen layoutid...")
    layouts = api_get_all("/layouts")
    layouts_dir = LOCAL_DIR / "layouts"
    layouts_dir.mkdir(exist_ok=True)

    manifest = {}

    for layout in layouts:
        folder = "components" if layout.get("component") else "layouts"
        folder_path = LOCAL_DIR / folder
        folder_path.mkdir(exist_ok=True)

        filename = f"{layout['title']}.tpl"
        filepath = folder_path / filename
        # /layouts list endpoint ei tagasta body välja — päritse üksikult
        detail = api_get(f"/layouts/{layout['id']}")
        body = detail.get("body", "") or ""
        filepath.write_text(body, encoding="utf-8")

        manifest[str(filepath.relative_to(LOCAL_DIR))] = {
            "id": layout["id"],
            "type": "layout",
            "updated_at": layout.get("updated_at", ""),
        }
        print(f"  ✓ {folder}/{filename}")

    # 2. Layout assets (CSS, JS, pildid, fondid)
    print("\n🎨 Laen layout assets...")
    assets = api_get_all("/layout_assets")

    for asset in assets:
        asset_type = asset.get("asset_type", "unknown")
        folder_name = ASSET_TYPE_TO_FOLDER.get(asset_type, "assets")
        folder_path = LOCAL_DIR / folder_name
        folder_path.mkdir(exist_ok=True)

        filename = asset["filename"]
        filepath = folder_path / filename

        if asset.get("editable", False) or asset_type in ("stylesheet", "javascript"):
            # Tekstifail — sisu on API-s
            asset_detail = api_get(f"/layout_assets/{asset['id']}")
            content = asset_detail.get("data", "") or ""
            filepath.write_text(content, encoding="utf-8")
        else:
            # Binaarfail (pilt, font) — laeme public URL-ilt
            public_url = asset.get("public_url", "")
            if public_url:
                try:
                    with urllib.request.urlopen(public_url) as resp:
                        filepath.write_bytes(resp.read())
                except Exception as e:
                    print(f"  ⚠ Ei saanud laadida {filename}: {e}")
                    continue

        manifest[str(filepath.relative_to(LOCAL_DIR))] = {
            "id": asset["id"],
            "type": "layout_asset",
            "asset_type": asset_type,
            "updated_at": asset.get("updated_at", ""),
        }
        print(f"  ✓ {folder_name}/{filename}")

    # 3. site.data varukoopia (ei ole template'i osa, elab ainult API-s)
    print("\n💾 Salvestan site.data varukoopia...")
    try:
        site_resp = api_get("/site")
        site_data = site_resp.get("data", {})
        sitedata_path = LOCAL_DIR / "site-data.json"
        sitedata_path.write_text(json.dumps(site_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  ✓ site-data.json ({len(site_data)} võtit)")
    except Exception as e:
        print(f"  ⚠ site.data varukoopia ebaõnnestus: {e}")

    # Salvesta manifest
    manifest_path = LOCAL_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Kõik failid salvestatud: {LOCAL_DIR}")
    print(f"   Layoutid: {sum(1 for v in manifest.values() if v['type'] == 'layout')}")
    print(f"   Assets:   {sum(1 for v in manifest.values() if v['type'] == 'layout_asset')}")

# --- Push ---

def push(target_files=None):
    manifest_path = LOCAL_DIR / "manifest.json"
    if not manifest_path.exists():
        print("❌ manifest.json puudub. Käivita esmalt: python3 voog.py pull")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())

    if target_files:
        files_to_push = {}
        not_found = []
        for target_file in target_files:
            rel_path = str(Path(target_file).relative_to(LOCAL_DIR)) if Path(target_file).is_absolute() else target_file
            if rel_path in manifest:
                files_to_push[rel_path] = manifest[rel_path]
            else:
                not_found.append(rel_path)
        if not_found:
            for nf in not_found:
                print(f"❌ Faili ei leitud manifestis: {nf}")
            if not files_to_push:
                sys.exit(1)
            # Osaline match — küsi kinnitust
            print(f"Jätkan {len(files_to_push)} failiga? (j/e) ", end="", flush=True)
            if input().strip().lower() not in ("j", "y", "yes", "jah"):
                print("Katkestatud.")
                return
    else:
        # Kõik failid — küsi kinnitust
        text_files = [p for p in manifest if Path(p).suffix.lower() not in
                      {'.woff', '.woff2', '.ttf', '.otf', '.eot',
                       '.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico'}]
        print(f"⚠ Pushid KÕIK {len(text_files)} tekstifaili. Oled kindel? (j/e) ", end="", flush=True)
        if input().strip().lower() not in ("j", "y", "yes", "jah"):
            print("Katkestatud.")
            return
        files_to_push = manifest

    # ⚠️ VoogStyle hoiatus: template-cs-*.tpl failide pushimine resetib
    # Voog'i salvestatud disainiredaktori kohandused tagasi template'i
    # vaikeväärtustele. Veendu enne pushimist, et vaikeväärtused vastavad
    # soovitud live-välimusele. Vt site-data.json õigete väärtuste jaoks.
    voogstyle_files = [p for p in files_to_push if "template-cs-" in p]
    if voogstyle_files:
        print(f"  ⚠ VoogStyle failid: {', '.join(voogstyle_files)}")
        print(f"    Push RESETIB salvestatud kohandused! Kontrolli vaikeväärtusi.")

    print(f"Laen üles {len(files_to_push)} faili...")

    BINARY_EXTS = {'.woff', '.woff2', '.ttf', '.otf', '.eot',
                   '.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico'}

    ok_count = 0
    fail_count = 0

    for rel_path, info in files_to_push.items():
        filepath = LOCAL_DIR / rel_path
        if not filepath.exists():
            print(f"  ⚠ Fail puudub: {rel_path}")
            continue

        if filepath.suffix.lower() in BINARY_EXTS:
            print(f"  — {rel_path} (binaarne, jätan vahele)")
            continue

        try:
            content = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"  — {rel_path} (binaarne, jätan vahele)")
            continue
        file_id = info["id"]
        file_type = info["type"]

        try:
            if file_id is None and file_type == "layout_asset":
                # New asset — create via POST
                asset_type = info.get("asset_type", "javascript")
                filename = rel_path.split("/")[-1]
                result = api_post("/layout_assets", {
                    "filename": filename,
                    "asset_type": asset_type,
                    "data": content,
                })
                # Update manifest with new ID
                new_id = result.get("id")
                if new_id:
                    info["id"] = new_id
                    manifest[rel_path]["id"] = new_id
                    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
                print(f"  ✓ {rel_path} (uus, id:{new_id})")
                ok_count += 1
            elif file_type == "layout":
                api_put(f"/layouts/{file_id}", {"body": content})
                print(f"  ✓ {rel_path}")
                ok_count += 1
            elif file_type == "layout_asset":
                api_put(f"/layout_assets/{file_id}", {"data": content})
                print(f"  ✓ {rel_path}")
                ok_count += 1
        except Exception as e:
            print(f"  ❌ {rel_path}: {e}")
            fail_count += 1

    if fail_count:
        print(f"⚠ Valmis: {ok_count} õnnestus, {fail_count} ebaõnnestus")
        sys.exit(1)
    else:
        print(f"✅ Valmis! ({ok_count} faili)")

# --- List ---

def list_files():
    manifest_path = LOCAL_DIR / "manifest.json"
    if not manifest_path.exists():
        print("❌ manifest.json puudub. Käivita esmalt: python3 voog.py pull")
        return
    manifest = json.loads(manifest_path.read_text())
    print(f"Kokku {len(manifest)} faili:\n")
    for path in sorted(manifest.keys()):
        info = manifest[path]
        print(f"  {path}  (id:{info['id']})")

# --- Tooted ---

def products_list():
    """Kõik tooted lühidalt."""
    page = 1
    all_products = []
    while True:
        data = api_get("/products", {"per_page": 100, "page": page, "include": "translations"}, base=ECOMMERCE_URL)
        if not data:
            break
        batch = data if isinstance(data, list) else data.get("products", [])
        if not batch:
            break
        all_products.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    print(f"{'ID':<12} {'Slug':<40} {'Nimi'}")
    print("-" * 80)
    for p in all_products:
        pid = str(p.get("id", ""))
        slug = p.get("slug", "") or ""
        name = p.get("name", "") or ""
        # Eemalda zero-width characters printimiseks
        name_clean = name.replace("\ufeff", "").replace("\u200b", "")
        print(f"{pid:<12} {slug:<40} {name_clean}")
    print(f"\nKokku: {len(all_products)} toodet")


def product_get(product_id):
    """Ühe toote täisinfo koos tõlgetega."""
    p = api_get(f"/products/{product_id}", {"include": "translations"}, base=ECOMMERCE_URL)
    print(f"ID:     {p.get('id')}")
    print(f"Nimi:   {repr(p.get('name', ''))}")
    print(f"Slug:   {p.get('slug', '')}")
    print(f"SKU:    {p.get('sku', '')}")
    print(f"Staatus: {p.get('status', '')}")
    tr = p.get("translations") or {}
    if tr:
        print("\nTõlked:")
        for field, langs in tr.items():
            if field in ("name", "slug"):
                print(f"  {field}:")
                if isinstance(langs, dict):
                    for lang, val in langs.items():
                        val_clean = (val or "").replace("\ufeff", "").replace("\u200b", "")
                        print(f"    {lang}: {repr(val_clean)}")


def product_update(product_id, pairs):
    """
    Uuenda toote välju.
    pairs = [("name-et", "Trippelgänger"), ("slug-en", "trippelganger"), ...]
    """
    translations = {"name": {}, "slug": {}}

    for key, val in pairs:
        if "-" not in key:
            print(f"❌ Tundmatu väli: {key}. Kasuta kujul: name-et, name-en, slug-et, slug-en")
            return
        field, lang = key.split("-", 1)
        if field not in ("name", "slug"):
            print(f"❌ Tundmatu väli: {field}. Lubatud: name, slug")
            return
        translations[field][lang] = val

    # Eemalda tühjad
    translations = {k: v for k, v in translations.items() if v}

    payload = {"product": {"translations": translations}}
    result = api_put(f"/products/{product_id}", payload, base=ECOMMERCE_URL)

    print(f"✅ Uuendatud:")
    print(f"   Nimi:  {repr(result.get('name', ''))}")
    print(f"   Slug:  {result.get('slug', '')}")
    # Kontrolli tõlked
    updated = api_get(f"/products/{product_id}", {"include": "translations"}, base=ECOMMERCE_URL)
    tr = updated.get("translations") or {}
    for field in ("name", "slug"):
        if field in tr:
            vals = tr[field]
            print(f"   {field}: " + ", ".join(f"{l}={repr(v)}" for l, v in (vals or {}).items()))


# --- Toote pildid ---

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def upload_asset(filepath):
    """Laeb pildi üles Voog asseti kaudu (3-sammuline protsess).
    Tagastab asset dict {id, url, width, height}."""
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Faili ei leitud: {filepath}")

    filename = filepath.name
    ext = filepath.suffix.lower()
    content_type = CONTENT_TYPES.get(ext)
    if not content_type:
        raise ValueError(f"Toetamata failitüüp: {ext}. Lubatud: {', '.join(CONTENT_TYPES)}")

    size = filepath.stat().st_size

    # 1. Loo asset kirje
    asset = api_post("/assets", {
        "filename": filename,
        "content_type": content_type,
        "size": size,
    })
    asset_id = asset["id"]
    upload_url = asset["upload_url"]

    # 2. Lae fail S3-sse (raw binary PUT, mitte JSON)
    file_data = filepath.read_bytes()
    req = urllib.request.Request(upload_url, data=file_data, method="PUT")
    req.add_header("Content-Type", content_type)
    req.add_header("x-amz-acl", "public-read")
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"S3 upload ebaõnnestus: HTTP {resp.status}")

    # 3. Kinnita asset
    confirmed = api_put(f"/assets/{asset_id}/confirm")

    return {
        "id": asset_id,
        "url": confirmed.get("public_url", ""),
        "width": confirmed.get("width"),
        "height": confirmed.get("height"),
    }


def product_set_images(product_id, filepaths):
    """Vahetab toote pildid. Esimene pilt = põhipilt, ülejäänud = galerii."""
    # Kontrolli toodet
    prod = api_get(f"/products/{product_id}", base=ECOMMERCE_URL)
    print(f"Toode: {prod['name']} (id:{product_id})")
    old_ids = prod.get("asset_ids", [])
    if old_ids:
        print(f"  Vanad pildid: {old_ids}")

    asset_ids = []
    for fp in filepaths:
        path = Path(fp)
        print(f"  Laen üles: {path.name}...", end="", flush=True)
        asset = upload_asset(path)
        asset_ids.append(asset["id"])
        dims = f"{asset['width']}x{asset['height']}" if asset['width'] else "töötlemisel"
        print(f" ✓ (id:{asset['id']}, {dims})")

    # Uuenda toodet
    result = api_put(f"/products/{product_id}", {
        "image_id": asset_ids[0],
        "asset_ids": asset_ids,
    }, base=ECOMMERCE_URL)

    img = result.get("image", {})
    dims = f"{img['width']}x{img['height']}" if img.get("width") else "töötlemisel"
    print(f"\n✅ Pildid uuendatud!")
    print(f"   Põhipilt: id:{asset_ids[0]} ({dims})")
    print(f"   Kokku pilte: {len(asset_ids)}")
    print(f"   Asset ID-d: {result.get('asset_ids', [])}")


# --- Ümbersuunamised ---

def redirects_list():
    """Kõik redirect reeglid."""
    rules = api_get_all("/redirect_rules")
    if not rules:
        print("Ümbersuunamisi ei leitud.")
        return
    print(f"{'ID':<10} {'Tüüp':<6} {'Allikas':<55} Siht")
    print("-" * 110)
    for r in sorted(rules, key=lambda x: x.get("source", "")):
        rid = str(r.get("id", ""))
        rtype = str(r.get("redirect_type", ""))
        src = r.get("source", "")
        dst = r.get("destination", "")
        active = "" if r.get("active", True) else " [INACTIVE]"
        print(f"{rid:<10} {rtype:<6} {src:<55} {dst}{active}")
    print(f"\nKokku: {len(rules)} reeglit")


def redirect_add(source, destination, redirect_type=301):
    """Lisa uus redirect reegel."""
    try:
        rtype = int(redirect_type)
    except ValueError:
        print(f"❌ Vigane redirect tüüp: {redirect_type}. Kasuta: 301, 302, 307 või 410")
        return

    result = api_post("/redirect_rules", {
        "redirect_rule": {
            "source": source,
            "destination": destination,
            "redirect_type": rtype,
            "active": True,
        }
    })
    print(f"✅ Lisatud: {source} → {destination} ({rtype})")
    print(f"   ID: {result.get('id')}")


# --- Pages ---

def pages_list():
    """Listib kõik lehed: id, path, title, hidden, layout."""
    pages = api_get_all("/pages")
    print(f"📄 {len(pages)} lehte:")
    for p in sorted(pages, key=lambda x: x.get("path") or ""):
        pid = p.get("id")
        path = p.get("path") or "/"
        title = (p.get("title") or "").strip()[:40]
        hidden = "🔒 hidden" if p.get("hidden") else "        "
        layout_obj = p.get("layout") or {}
        layout = (
            p.get("layout_name")
            or p.get("layout_title")
            or (layout_obj.get("title") if isinstance(layout_obj, dict) else None)
            or "?"
        )
        print(f"  {hidden} {pid:>8} | /{path:<40} | {title:<40} | layout={layout}")


def page_get(page_id):
    """Näitab ühe lehe täisinfot."""
    p = api_get(f"/pages/{page_id}")
    print(f"📄 Page id={p.get('id')}")
    print(f"  title       : {p.get('title')}")
    print(f"  path        : /{p.get('path') or ''}")
    print(f"  hidden      : {p.get('hidden')}")
    print(f"  layout_id   : {p.get('layout_id')}")
    layout = p.get("layout_name") or p.get("layout_title") or (p.get("layout") or {}).get("title") or "?"
    print(f"  layout_name : {layout}")
    print(f"  content_type: {p.get('content_type')}")
    lang = p.get("language") or {}
    print(f"  language    : {lang.get('code')} (id {lang.get('id')})")
    print(f"  parent_id   : {p.get('parent_id')}")
    print(f"  created_at  : {p.get('created_at')}")
    print(f"  updated_at  : {p.get('updated_at')}")
    print(f"  public_url  : {p.get('public_url')}")


def pages_snapshot(output_dir):
    """Backuppib kõik lehed + nende contents'i JSON-i kettale."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pages = api_get_all("/pages")
    pages_path = out / "pages.json"
    pages_path.write_text(json.dumps(pages, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ pages.json: {len(pages)} lehte")

    for p in pages:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = api_get(f"/pages/{pid}/contents")
        except Exception as e:
            print(f"  ⚠ Page {pid} contents ebaõnnestus: {e}")
            continue
        contents_path = out / f"page_{pid}_contents.json"
        contents_path.write_text(json.dumps(contents, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅ Snapshot: {output_dir}")


# --- site-snapshot: comprehensive read-only backup of EVERY mutable resource ---

# Standard /admin/api/ list endpoints. Each is paginated via api_get_all.
# Order matters only for log readability — failures are independent.
SITE_SNAPSHOT_LIST_ENDPOINTS = [
    "/pages",
    "/articles",
    "/elements",
    "/element_definitions",
    "/layouts",
    "/layout_assets",
    "/languages",
    "/redirect_rules",
    "/nodes",
    "/texts",
    "/content_partials",
    "/tags",
    "/forms",
    "/media_sets",
    "/assets",
    "/webhooks",
]

# Standard /admin/api/ singletons (no list).
SITE_SNAPSHOT_SINGLETONS = ["/site", "/me"]


def _snapshot_filename_for(endpoint):
    """`/redirect_rules` -> `redirect_rules.json`."""
    return endpoint.lstrip("/").replace("/", "_") + ".json"


def _slugify_path(path):
    """URL path -> filename slug. Empty/`/` -> `home`. `/pood/kass` -> `pood-kass`."""
    p = (path or "").strip("/")
    if not p:
        return "home"
    cleaned = re.sub(r"[^a-z0-9-]+", "-", p.lower()).strip("-")
    return cleaned or "home"


def _pick_sample_page_paths(pages, max_samples=3):
    """Pick up to N representative URL paths to render for VoogStyle capture.

    Prefers front page (empty path) + variety across content_types, skips hidden.
    Returns list of URL paths starting with "/".
    """
    if not pages:
        return []
    visible = [p for p in pages if not p.get("hidden")] or list(pages)

    seen_urls = set()
    seen_cts = set()
    picks = []

    # 1. Front page (empty path) first
    for p in visible:
        if (p.get("path") or "").strip("/") == "":
            url = "/"
            seen_urls.add(url)
            seen_cts.add(p.get("content_type") or "default")
            picks.append(url)
            break

    # 2. One per NEW content_type — prioritize variety over duplicating types
    by_ct = {}
    for p in visible:
        if (p.get("path") or "").strip("/") == "":
            continue
        ct = p.get("content_type") or "default"
        by_ct.setdefault(ct, []).append(p)
    for ct, items in by_ct.items():
        if len(picks) >= max_samples:
            break
        if ct in seen_cts:
            continue
        url = "/" + (items[0].get("path") or "").strip("/")
        if url not in seen_urls:
            seen_urls.add(url)
            seen_cts.add(ct)
            picks.append(url)

    # 3. Fill remaining slots with any other visible pages
    for p in visible:
        if len(picks) >= max_samples:
            break
        url = "/" + (p.get("path") or "").strip("/")
        if url == "/" or url in seen_urls:
            continue
        seen_urls.add(url)
        picks.append(url)

    return picks[:max_samples]


def _fetch_rendered_page(url):
    """Fetch a public Voog page as HTML string. Raises on HTTP/network error."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 voog.py-snapshot/1.0"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _snapshot_log_skip(label, exc):
    """Format a single skip log line for an endpoint that 404'd or errored."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return f"  — {label}: endpoint puudub (404)"
    if isinstance(exc, urllib.error.HTTPError):
        return f"  ⚠ {label}: HTTP {exc.code} {exc.reason}"
    return f"  ⚠ {label}: {exc}"


def site_snapshot(output_dir):
    """Comprehensive read-only backup of EVERY mutable Voog resource.

    Refuses to overwrite an existing directory. Per-resource graceful 404 handling
    means missing endpoints (e.g. /elements on a non-element site) are skipped, not
    fatal. Empty list endpoints write `[]` so absence vs empty is distinguishable.

    Required pre-flight before any risky operation: layout rename, mass push,
    layout swap, or VoogStyle template push.
    """
    out = Path(output_dir)
    if out.exists():
        sys.stderr.write(
            f"❌ Sihtkaust eksisteerib juba: {out}\n"
            "   Vali teine kaust või kustuta vana snapshot enne uue tegemist.\n"
        )
        sys.exit(1)
    out.mkdir(parents=True, exist_ok=False)

    print(f"📥 Site-snapshot: {HOST} → {out}/")
    written = 0
    pages_data = []
    articles_data = []

    # 1. Standard API list endpoints
    for endpoint in SITE_SNAPSHOT_LIST_ENDPOINTS:
        filename = _snapshot_filename_for(endpoint)
        try:
            data = api_get_all(endpoint)
        except Exception as e:
            print(_snapshot_log_skip(filename, e))
            continue
        (out / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  ✓ {filename} ({len(data)})")
        written += 1
        if endpoint == "/pages":
            pages_data = data
        elif endpoint == "/articles":
            articles_data = data

    # 2. Standard API singletons
    for endpoint in SITE_SNAPSHOT_SINGLETONS:
        filename = _snapshot_filename_for(endpoint)
        try:
            data = api_get(endpoint)
        except Exception as e:
            print(_snapshot_log_skip(filename, e))
            continue
        (out / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  ✓ {filename}")
        written += 1

    # 3. Per-page contents
    page_contents_count = 0
    for p in pages_data:
        pid = p.get("id")
        if not pid:
            continue
        try:
            contents = api_get(f"/pages/{pid}/contents")
        except Exception as e:
            print(f"  ⚠ page {pid} contents: {e}")
            continue
        (out / f"page_{pid}_contents.json").write_text(
            json.dumps(contents, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written += 1
        page_contents_count += 1
    if page_contents_count:
        print(f"  ✓ page contents × {page_contents_count}")

    # 4. Per-article details
    article_detail_count = 0
    for a in articles_data:
        aid = a.get("id")
        if not aid:
            continue
        try:
            detail = api_get(f"/articles/{aid}")
        except Exception as e:
            print(f"  ⚠ article {aid}: {e}")
            continue
        (out / f"article_{aid}.json").write_text(
            json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written += 1
        article_detail_count += 1
    if article_detail_count:
        print(f"  ✓ article details × {article_detail_count}")

    # 5. Ecommerce: products list + per-product details with translations
    products_data = []
    try:
        products_data = api_get_all("/products", base=ECOMMERCE_URL)
    except Exception as e:
        print(_snapshot_log_skip("products.json", e))

    if products_data:
        (out / "products.json").write_text(
            json.dumps(products_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  ✓ products.json ({len(products_data)})")
        written += 1
        product_detail_count = 0
        for prod in products_data:
            pid = prod.get("id")
            if not pid:
                continue
            try:
                detail = api_get(
                    f"/products/{pid}",
                    {"include": "variant_types,translations"},
                    base=ECOMMERCE_URL,
                )
            except Exception as e:
                print(f"  ⚠ product {pid}: {e}")
                continue
            (out / f"product_{pid}.json").write_text(
                json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            written += 1
            product_detail_count += 1
        if product_detail_count:
            print(f"  ✓ product details × {product_detail_count}")

    # 6. Rendered HTML for VoogStyle capture (the only way to see saved customizations)
    for path in _pick_sample_page_paths(pages_data):
        slug = _slugify_path(path)
        url = f"https://{HOST}{path}"
        try:
            html = _fetch_rendered_page(url)
        except Exception as e:
            print(f"  ⚠ {url}: {e}")
            continue
        (out / f"voog_style_rendered_{slug}.html").write_text(html, encoding="utf-8")
        print(f"  ✓ voog_style_rendered_{slug}.html")
        written += 1

    print(f"\n✅ Snapshot complete: {written} resources backed up to {out}/")


def layout_rename(layout_id, new_title):
    """Nimeta layout ümber API-s + lokaalses manifestis + failsüsteemis. Säilitab id."""
    layout_id = int(layout_id)
    if "/" in new_title or "\\" in new_title or new_title.startswith("."):
        print(f"❌ Layout title ei tohi sisaldada '/' ega '\\' ega alata punktiga: {new_title!r}")
        sys.exit(1)

    # 1. API call — PUT /layouts/{id} {"title": new_title}
    print(f"PUT /layouts/{layout_id} title=\"{new_title}\"...")
    api_put(f"/layouts/{layout_id}", {"title": new_title})

    # 2. Find old path in manifest
    manifest_path = LOCAL_DIR / "manifest.json"
    if not manifest_path.exists():
        print("⚠ manifest.json puudub — fail- ja manifest-uuendus jäeti vahele.")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    old_path = None
    folder = None
    for path, info in manifest.items():
        if info.get("id") == layout_id and info.get("type") == "layout":
            old_path = path
            folder = path.split("/", 1)[0]  # "layouts" or "components"
            break

    if old_path is None:
        print(f"⚠ Layout id {layout_id} manifestist ei leitud — ainult API uuendati.")
        return

    new_path = f"{folder}/{new_title}.tpl"

    # 3. Rename file on disk
    old_file = LOCAL_DIR / old_path
    new_file = LOCAL_DIR / new_path
    if old_file.exists():
        new_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.rename(new_file)
        print(f"  ✓ {old_path} → {new_path}")

    # 4. Update manifest
    info = manifest.pop(old_path)
    manifest[new_path] = info
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ manifest.json uuendatud")


def asset_replace(asset_id, new_filename):
    """Asenda layout_asset uue ID-ga (DELETE+POST workaround filename rename'iks).

    Voog API tagastab `PUT /layout_assets/{id}` koos `filename` väljaga HTTP 500.
    Workaround: GET vana asset → POST uus (uue id-ga) → uuenda manifest + lokaalne
    fail. VANA asset jääb alles — caller peab kõigepealt template'id uue nimega
    pushima, alles siis manuaalselt vana DELETE'ima.
    """
    asset_id = int(asset_id)
    if "/" in new_filename or "\\" in new_filename or new_filename.startswith("."):
        print(f"❌ Asset filename ei tohi sisaldada '/' ega '\\' ega alata punktiga: {new_filename!r}")
        sys.exit(1)

    # 1. GET vana asseti metadata + sisu
    print(f"GET /layout_assets/{asset_id}...")
    old_asset = api_get(f"/layout_assets/{asset_id}")
    asset_type = old_asset.get("asset_type")
    old_filename = old_asset.get("filename")
    content = old_asset.get("data")

    # 2. Leia manifest entry, et teada folder + lokaalne fail
    manifest_path = LOCAL_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    old_path = None
    folder = None
    for path, info in manifest.items():
        if info.get("id") == asset_id and info.get("type") == "layout_asset":
            old_path = path
            folder = path.split("/", 1)[0]
            break

    # Sisu fallback: kui GET ei tagastanud `data` välja, loe lokaalsest failist
    if content is None and old_path is not None:
        local_file = LOCAL_DIR / old_path
        if local_file.exists():
            content = local_file.read_text(encoding="utf-8")

    if content is None:
        print(f"❌ Ei suutnud lugeda asset {asset_id} sisu (ei API ega lokaalne fail)")
        sys.exit(1)

    # 3. POST uus asset
    print(f"POST /layout_assets filename=\"{new_filename}\"...")
    result = api_post("/layout_assets", {
        "filename": new_filename,
        "asset_type": asset_type,
        "data": content,
    })
    new_id = result.get("id")
    if not new_id:
        print(f"❌ POST vastus ei sisaldanud uut id-d: {result!r}")
        sys.exit(1)

    # 4. Uuenda manifest + lokaalne fail
    if old_path and folder:
        new_path = f"{folder}/{new_filename}"
        old_file = LOCAL_DIR / old_path
        new_file = LOCAL_DIR / new_path
        if old_file.exists():
            new_file.parent.mkdir(parents=True, exist_ok=True)
            old_file.rename(new_file)
        manifest.pop(old_path, None)
        manifest[new_path] = {
            "id": new_id,
            "type": "layout_asset",
            "asset_type": asset_type,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  ✓ Asset replaced: {old_path} (id {asset_id}) → {new_path} (id {new_id})")
    else:
        print(f"  ✓ POST õnnestus: uus id {new_id} (manifestit ei uuendatud — vana entry'd ei leitud)")

    # 5. Hoiatus: vana asset on alles
    print(f"⚠ Old asset id {asset_id} (filename {old_filename!r}) is still present.")
    print(f"  After updating + pushing templates that reference the old name, delete with:")
    print(f"  curl -X DELETE 'https://{HOST}/admin/api/layout_assets/{asset_id}' \\")
    print(f"       -H \"X-API-Token: $RUNNEL_VOOG_API_KEY\"")


def layout_create(kind, file_path):
    """Create a new layout or component in Voog via POST /admin/api/layouts.

    kind: "layout" or "component" — sets API field component=false/true.
    file_path: path relative to repo root, e.g. "layouts/Front page.tpl"
               or "components/site-header.tpl".

    On success: POSTs body, captures returned id, updates manifest.json.
    """
    if kind not in ("layout", "component"):
        print(f"❌ kind peab olema 'layout' või 'component', sain: {kind}")
        sys.exit(1)

    rel_path = file_path.lstrip("./")
    full_path = LOCAL_DIR / rel_path
    if not full_path.exists():
        print(f"❌ Fail puudub: {full_path}")
        sys.exit(1)

    body = full_path.read_text(encoding="utf-8")
    title = full_path.stem  # filename without extension

    payload = {
        "title": title,
        "body": body,
        "component": (kind == "component"),
    }

    print(f"POST /layouts title={title!r} component={kind == 'component'}...")
    result = api_post("/layouts", payload)
    new_id = result.get("id")
    if not new_id:
        print(f"❌ POST vastus ei sisaldanud uut id-d: {result!r}")
        sys.exit(1)

    manifest_path = LOCAL_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[rel_path] = {
        "id": new_id,
        "type": "layout",
        "updated_at": result.get("updated_at", ""),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"  ✓ Loodi {kind}: {rel_path} (id:{new_id})")
    print(f"  Manifest uuendatud.")
    return new_id


def page_set_hidden(page_ids, hidden):
    """Bulk toggle hidden flag paljudele lehtedele. Exitib != 0 kui mõni ebaõnnestus."""
    flag = "🔒 hidden" if hidden else "👁  visible"
    print(f"Lülitan {len(page_ids)} lehte: {flag}")
    fail_count = 0
    for pid in page_ids:
        try:
            api_put(f"/pages/{pid}", {"hidden": bool(hidden)})
            print(f"  ✓ {pid}")
        except Exception as e:
            print(f"  ✗ {pid}: {e}")
            fail_count += 1
    if fail_count:
        print(f"⚠ {fail_count}/{len(page_ids)} ebaõnnestus")
        sys.exit(1)


def page_set_layout(page_id, layout_id):
    """Muudab lehe layout_id'd."""
    layout_id = int(layout_id)
    print(f"PUT /pages/{page_id} layout_id={layout_id}...")
    api_put(f"/pages/{page_id}", {"layout_id": layout_id})
    print(f"  ✓ page {page_id} → layout {layout_id}")


def page_delete(page_id, force=False):
    """Kustutab lehe (irreversibel). force=True skipib kinnituse."""
    if not force:
        # Näita lehe info enne kustutamist
        try:
            p = api_get(f"/pages/{page_id}")
            print(f"⚠ Kustutan: id={page_id} title=\"{p.get('title')}\" path=/{p.get('path') or ''}")
        except Exception:
            print(f"⚠ Kustutan: id={page_id} (info'i ei saadud)")
        print("Kinnitad? (j/e) ", end="", flush=True)
        if input().strip().lower() not in ("j", "y", "yes", "jah"):
            print("Katkestatud.")
            return
    try:
        api_delete(f"/pages/{page_id}")
        print(f"  ✓ page {page_id} kustutatud")
    except Exception as e:
        print(f"  ✗ page {page_id} kustutamine ebaõnnestus: {e}")
        sys.exit(1)


def pages_pull():
    """Salvestab lokaalseks lihtsustatud pages.json — struktuur ilma sisuta."""
    pages = api_get_all("/pages")
    simplified = []
    for p in pages:
        lang = p.get("language") or {}
        layout = p.get("layout") or {}
        simplified.append({
            "id": p.get("id"),
            "path": p.get("path"),
            "title": p.get("title"),
            "hidden": p.get("hidden"),
            "layout_id": p.get("layout_id") or layout.get("id"),
            "layout_name": p.get("layout_name") or p.get("layout_title") or layout.get("title"),
            "content_type": p.get("content_type"),
            "parent_id": p.get("parent_id"),
            "language_code": lang.get("code"),
            "public_url": p.get("public_url"),
        })
    pages_path = LOCAL_DIR / "pages.json"
    pages_path.write_text(json.dumps(simplified, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ pages.json salvestatud ({len(simplified)} lehte)")


# --- Serve (lokaalne proxy) ---

# JS/CSS failid, mida proxy asendab kohalike versioonidega.
# Võti: failinimi (URL-ist leitav), väärtus: kohalik tee LOCAL_DIR suhtes.
LOCAL_ASSETS = {
    # JS
    "application.min.js": "javascripts/application.min.js",
    "application.js": "javascripts/application.js",
    "editmode.min.js": "javascripts/editmode.min.js",
    "editmode.js": "javascripts/editmode.js",
    "cart.js": "javascripts/cart.js",
    "tracking.js": "javascripts/tracking.js",
    "sold-out-notify.js": "javascripts/sold-out-notify.js",
    "stella-id.js": "javascripts/stella-id.js",
    "search-drawer.js": "javascripts/search-drawer.js",
    "newsletter-drawer.js": "javascripts/newsletter-drawer.js",
    "empty-cart.js": "javascripts/empty-cart.js",
    "buy-together.js": "javascripts/buy-together.js",
    "additional-editmode.js": "javascripts/additional-editmode.js",
    "custom-buy-button-manager.min.js": "javascripts/custom-buy-button-manager.min.js",
    "custom-buy-button-manager.js": "javascripts/custom-buy-button-manager.js",
    # CSS
    "main.min.css": "stylesheets/main.min.css",
    "main.css": "stylesheets/main.css",
    "additional.css": "stylesheets/additional.css",
    "cart.css": "stylesheets/cart.css",
    "stella-id.css": "stylesheets/stella-id.css",
}

# Regex: leiab src="...filename.js?v=..." ja href="...filename.css?v=..." HTML-ist.
# Asendab tuntud failinimed kohalike /_local/ versioonidega.
def _build_asset_pattern():
    names = "|".join(re.escape(n) for n in LOCAL_ASSETS)
    return re.compile(
        r'((?:src|href)\s*=\s*["\'])'  # group 1: atribuut + avav jutumärk
        r'[^"\']*?'                      # path enne failinime
        r'(' + names + r')'              # group 2: failinimi
        r'(?:\?[^"\']*)?'               # valikuline ?v=... parameeter
        r'(["\'])',                       # group 3: sulgev jutumärk
        re.IGNORECASE,
    )

_ASSET_RE = _build_asset_pattern()


def _fetch_live(path):
    """Laeb lehe live-saidilt. Tagastab (status, content_type, body_bytes)."""
    url = f"https://{HOST}{path}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 voog.py-serve/1.0")
    req.add_header("Accept", "text/html,application/xhtml+xml,*/*")
    # Voog võib SSL sertifikaadi osas olla range — kasuta vaikimisi konteksti
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            ct = resp.headers.get("Content-Type", "text/html")
            body = resp.read()
            return (resp.status, ct, body)
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        ct = e.headers.get("Content-Type", "text/html") if hasattr(e, "headers") else "text/html"
        return (e.code, ct, body)


def _inject_local_assets(html_bytes):
    """Asendab HTML-is tuntud JS/CSS URL-id kohalike /_local/ versioonidega."""
    try:
        html = html_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return html_bytes

    def _replace(m):
        attr_prefix = m.group(1)   # src=" või href="
        filename = m.group(2)      # additional.js
        quote = m.group(3)         # " või '
        local_path = LOCAL_ASSETS[filename]
        return f'{attr_prefix}/_local/{local_path}{quote}'

    modified = _ASSET_RE.sub(_replace, html)

    # Lisa väike visuaalne indikaator, et see on lokaalne proxy
    indicator = (
        '<div style="position:fixed;bottom:8px;right:8px;background:#1a1a2e;color:#e94560;'
        'padding:4px 10px;border-radius:4px;font:bold 11px/1.4 monospace;z-index:99999;'
        'pointer-events:none;opacity:0.85">LOCAL DEV</div>'
    )
    modified = modified.replace("</body>", f"{indicator}</body>", 1)

    return modified.encode("utf-8")


class _ProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler: serveerib kohalikke faile /_local/ alt, ülejäänu proxib live-saidilt."""

    def do_GET(self):
        # 1. Kohalikud failid
        if self.path.startswith("/_local/"):
            self._serve_local()
            return

        # 2. Proxy live-saidilt
        status, ct, body = _fetch_live(self.path)

        # HTML vastuse puhul asenda asset URL-id
        if "text/html" in ct:
            body = _inject_local_assets(body)

        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # POST päringud (nt cart API) — proxy otse läbi
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len) if content_len else b""

        url = f"https://{HOST}{self.path}"
        req = urllib.request.Request(url, data=post_body, method="POST")
        req.add_header("User-Agent", "Mozilla/5.0 voog.py-serve/1.0")
        # Kopeeri olulised päised
        for h in ("Content-Type", "Accept", "X-Requested-With"):
            val = self.headers.get(h)
            if val:
                req.add_header(h, val)

        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                ct = resp.headers.get("Content-Type", "application/json")
                body = resp.read()
                self.send_response(resp.status)
        except urllib.error.HTTPError as e:
            ct = e.headers.get("Content-Type", "application/json") if hasattr(e, "headers") else "application/json"
            body = e.read() if hasattr(e, "read") else b""
            self.send_response(e.code)

        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_local(self):
        """Serveerib faili kohalikust stellasoomlais-voog/ kaustast."""
        rel_path = self.path[len("/_local/"):]  # eemalda /_local/ prefix
        # Turvakontroll — ära luba .. navigeerimist
        if ".." in rel_path:
            self.send_error(403, "Keelatud")
            return

        filepath = LOCAL_DIR / rel_path
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404, f"Faili ei leitud: {rel_path}")
            return

        # MIME tüübi tuvastamine
        ext = filepath.suffix.lower()
        mime_map = {
            ".js": "application/javascript",
            ".css": "text/css",
            ".html": "text/html",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
        }
        ct = mime_map.get(ext, "application/octet-stream")

        body = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Kompaktsem logimine."""
        # BaseHTTPRequestHandler kutsub log_message erinevate argumentidega:
        # do_GET/POST: format="%s", args=("GET /path HTTP/1.1", "200", "-")
        # send_error:  format="code %d, message %s", args=(404, "Not Found")
        try:
            msg = format % args
        except (TypeError, ValueError):
            msg = str(args)
        # Tõmba path välja kui võimalik
        if self.path.startswith("/_local/"):
            marker = "📁"
        else:
            marker = "🌐"
        sys.stderr.write(f"  {marker} {msg}\n")


def serve(port=8080):
    """Käivitab lokaalse proxy serveri."""
    print(f"🚀 Voog lokaalne arendusserver")
    print(f"   Live sait: https://{HOST}")
    print(f"   Lokaalsed failid: {LOCAL_DIR}")
    print(f"\n   Asendatavad failid:")
    for name, path in LOCAL_ASSETS.items():
        local_file = LOCAL_DIR / path
        exists = "✓" if local_file.exists() else "✗ PUUDUB"
        print(f"     {name} → /_local/{path} [{exists}]")
    print(f"\n   Ava brauseris: http://localhost:{port}")
    print(f"   Peata: Ctrl+C\n")

    server = HTTPServer(("", port), _ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Server peatatud.")
        server.server_close()


# --- Main ---

def main():
    """Parse argv, dispatch to command. Side effect: loads site config (except for help)."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd in _HELP_CMDS:
        print(__doc__)
        return
    init_site()

    if cmd == "pull":
        pull()
    elif cmd == "serve":
        port = 8080
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
            elif arg.isdigit():
                port = int(arg)
        serve(port)
    elif cmd == "push":
        targets = sys.argv[2:] if len(sys.argv) > 2 else None
        push(targets)
    elif cmd == "list":
        list_files()
    elif cmd == "products":
        products_list()
    elif cmd == "product":
        if len(sys.argv) < 3:
            print("Kasutus: python3 voog.py product <id> [väli väärtus ...]")
            sys.exit(1)
        pid = sys.argv[2]
        extra = sys.argv[3:]
        if not extra:
            product_get(pid)
        elif len(extra) % 2 != 0:
            print("❌ Välja-väärtuse paarid peavad olema paaris arv argumente")
            sys.exit(1)
        else:
            pairs = [(extra[i], extra[i+1]) for i in range(0, len(extra), 2)]
            product_update(pid, pairs)
    elif cmd == "product-image":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py product-image <id> <fail> [fail2 ...]")
            sys.exit(1)
        pid = sys.argv[2]
        files = sys.argv[3:]
        product_set_images(pid, files)
    elif cmd == "redirects":
        redirects_list()
    elif cmd == "redirect-add":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py redirect-add <allikas> <siht> [301|302|307|410]")
            sys.exit(1)
        rtype = sys.argv[4] if len(sys.argv) > 4 else 301
        redirect_add(sys.argv[2], sys.argv[3], rtype)
    elif cmd == "pages":
        pages_list()
    elif cmd == "page":
        if len(sys.argv) < 3:
            print("Kasutus: python3 voog.py page <id>")
            sys.exit(1)
        page_get(sys.argv[2])
    elif cmd == "pages-snapshot":
        if len(sys.argv) < 3:
            print("Kasutus: python3 voog.py pages-snapshot <output-dir>")
            sys.exit(1)
        pages_snapshot(sys.argv[2])
    elif cmd == "site-snapshot":
        if len(sys.argv) < 3:
            print("Kasutus: python3 voog.py site-snapshot <output-dir>")
            sys.exit(1)
        site_snapshot(sys.argv[2])
    elif cmd == "layout-rename":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py layout-rename <id> <uus-tiitel>")
            sys.exit(1)
        layout_rename(sys.argv[2], sys.argv[3])
    elif cmd == "asset-replace":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py asset-replace <id> <uus-filename>")
            sys.exit(1)
        asset_replace(sys.argv[2], sys.argv[3])
    elif cmd == "layout-create":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py layout-create <kind> <path>")
            print("  kind: layout | component")
            sys.exit(1)
        layout_create(sys.argv[2], sys.argv[3])
    elif cmd == "page-set-hidden":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py page-set-hidden <id> [<id>...] true|false")
            sys.exit(1)
        last = sys.argv[-1].lower()
        if last not in ("true", "false"):
            print("Viimane argument peab olema 'true' või 'false'")
            sys.exit(1)
        ids = sys.argv[2:-1]
        page_set_hidden(ids, last == "true")
    elif cmd == "page-set-layout":
        if len(sys.argv) < 4:
            print("Kasutus: python3 voog.py page-set-layout <page-id> <layout-id>")
            sys.exit(1)
        page_set_layout(sys.argv[2], sys.argv[3])
    elif cmd == "page-delete":
        if len(sys.argv) < 3:
            print("Kasutus: python3 voog.py page-delete <id> [--force]")
            sys.exit(1)
        force = "--force" in sys.argv
        page_delete(sys.argv[2], force=force)
    elif cmd == "pages-pull":
        pages_pull()
    else:
        print(f"❌ Tundmatu käsk: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
