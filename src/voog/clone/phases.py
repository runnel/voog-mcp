"""The clone phases: what actually gets copied, in what order, and why.

Each phase is a function taking a :class:`CloneContext` and returning a
:class:`PhaseReport`. They run in the order listed in :data:`PHASE_ORDER`
because each depends on the id maps written by the ones before it — you
cannot point a page at a layout before the layout exists, and you cannot
rebuild a gallery before its images have target ids.

Every phase obeys three rules:

  1. **Resume, don't repeat.** Consult the state map first; skip what is
     already mapped.
  2. **Record before continuing.** The map entry is written as soon as the
     target object exists, not at the end of the loop.
  3. **Report, don't guess.** Anything that could not be copied lands in
     ``report.problems`` with the reason. A clone that quietly omits 40 of
     617 images is worse than one that says it did.

Four Voog limits are structural and no amount of care removes them; they
are surfaced as notes rather than worked around (see ``KNOWN_LIMITS``).
"""

from __future__ import annotations

import logging
import mimetypes
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from threading import Lock

from voog._concurrency import parallel_map, propagate_tool_context
from voog._ordering import put_ordered_with_readback
from voog._upload_validation import _validate_upload_url
from voog.clone.rewrite import UrlRewriter, media_prefix_of

logger = logging.getLogger("voog.clone")

PHASE_ORDER = (
    "layouts",
    "layout_assets",
    "assets",
    "site",
    "pages",
    "contents",
    "articles",
    "cleanup",
    "verify",
)

# Phases that mutate the target. ``plan`` is not here because it is a
# read-only preflight, and ``verify`` only reads.
MUTATING_PHASES = frozenset(PHASE_ORDER) - {"verify"}

# Text-ish layout asset extensions. Anything else goes up as a binary
# multipart POST, because `data` is a UTF-8 string field and a favicon is
# not UTF-8.
TEXT_ASSET_EXTENSIONS = {".css", ".js", ".svg", ".xml", ".json", ".txt", ".html", ".map"}

# Voog rejects these on write; the clone states the deviation rather than
# pretending. All four confirmed on the 2026-08-04 kolmkoma duplication.
KNOWN_LIMITS = (
    "created_at is not settable — PUT returns 200 and the value resets to now, "
    "so every copied article and page carries the clone date.",
    "published_at is not settable, for the same reason.",
    "Duplicate article paths cannot be reproduced — Voog now auto-suffixes a "
    "colliding path with -1. Legacy sites that hold two articles on one path "
    "will have the twin land on a different URL.",
    "Asset storage is capped per plan. The clone checks the target's remaining "
    "quota before uploading and stops cleanly rather than hitting 422 "
    "quota_exceeded partway through.",
)

# site.data keys Voog owns; PUTting them back is rejected or meaningless.
_INTERNAL_DATA_PREFIX = "internal_"

# Site settings worth carrying. Deliberately a whitelist: PUT /site accepts
# far more, including domain and plan fields that must never be copied
# between sites.
_SITE_SETTINGS_FIELDS = (
    "title_format",
    "title_separator",
    "meta_keywords",
    "search_enabled",
    "sitemap_enabled",
    "sitemap_inc_articles",
    "sitemap_inc_products",
    "sitemap_inc_tags",
)


@dataclass
class PhaseReport:
    phase: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def problem(self, what: str, reason: str) -> None:
        self.problems.append({"what": what, "reason": reason})

    def to_dict(self) -> dict:
        out = {
            "phase": self.phase,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }
        if self.problems:
            out["problems"] = self.problems
        if self.notes:
            out["notes"] = self.notes
        if self.details:
            out.update(self.details)
        return out


class _AssetBudget:
    """Serialises the "is there room for this upload?" decision.

    Uploads run in parallel, so without a single reservation point N workers
    can each look at the same remaining quota, each conclude their file
    fits, and collectively overshoot it — turning a clean stop into a batch
    of 422s partway through the protocol, some of which leave a created-but-
    unconfirmed asset behind. Reserving before the download means the
    accounting is decided by one thread at a time.
    """

    def __init__(self, limit_bytes: int | None):
        self.limit = limit_bytes
        self.spent = 0
        self.exhausted = False
        self._lock = Lock()

    def reserve(self, size: int) -> bool:
        if self.limit is None:
            # Unknown quota: upload until Voog refuses. `exhausted` is still
            # honoured so the first real 422 stops the rest of the batch.
            return not self.exhausted
        with self._lock:
            if self.exhausted or self.spent + size > self.limit:
                self.exhausted = True
                return False
            self.spent += size
            return True

    def stop(self) -> None:
        self.exhausted = True


@dataclass
class CloneContext:
    source: object  # VoogClient
    target: object  # VoogClient
    state: object  # CloneState
    source_name: str
    target_name: str
    dry_run: bool = True
    asset_budget_bytes: int | None = None
    max_workers: int = 4
    _rewriter: UrlRewriter | None = None

    # ------------------------------------------------------------ helpers
    def rewriter(self) -> UrlRewriter:
        """Build (once) the URL rewriter for this pair of sites.

        Derived from live data on both sides: the media prefix comes from
        whichever asset the target already has (the asset phase runs first,
        so by the time contents need it there is at least one), and the
        source hostnames from the source site record.
        """
        if self._rewriter is None:
            src_site = self.source.get("/site")
            src_hosts = [
                h
                for h in (
                    src_site.get("primary_domain"),
                    _host_of(src_site.get("public_url")),
                    getattr(self.source, "host", None),
                )
                if h
            ]
            self._rewriter = UrlRewriter(
                source_hosts=src_hosts,
                source_media_prefix=media_prefix_of(self.source.get("/assets?per_page=1")),
                target_media_prefix=media_prefix_of(self.target.get("/assets?per_page=1")),
            )
        return self._rewriter


def _host_of(url: str | None) -> str | None:
    match = re.match(r"https?://([^/]+)", url or "")
    return match.group(1) if match else None


def _download(url: str, *, timeout: int = 300) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "voog-mcp-clone"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def describe_exc(exc: Exception) -> str:
    """Render an exception WITH the server's explanation, not just the status.

    httpx's ``HTTPStatusError`` stringifies to "Client error '422
    Unprocessable Entity' for url ..." and drops the response body — which
    is where Voog puts the only useful part. A real example from building
    this module: ``{"errors":{"layout_id":["not in available layouts
    list"]}}``, invisible in the default message and the entire diagnosis.
    A clone reports dozens of per-object problems; each one costing a
    hand-run probe to interpret is not a report, it is a to-do list.
    """
    text = str(exc)
    response = getattr(exc, "response", None)
    body = None
    if response is not None:
        try:
            body = response.text
        except Exception:  # pragma: no cover — defensive
            body = None
    if body:
        body = " ".join(body.split())[:400]
        if body not in text:
            return f"{text} — {body}"
    return text


# ===================================================================== plan


def quota_of(client) -> dict:
    """Remaining asset storage on a site, as far as Voog will tell us.

    ``GET /site`` reports ``data_usage`` in bytes, and a trial/developer site
    carries its cap in ``data.internal_trial_assets_quota`` (5 GB on the
    kolm-koma-2026 test site, against 1.23 GB used). A site with no cap key
    reports ``limit: None``, which callers must read as "unknown", NOT as
    "unlimited" — the free-plan run that motivated this hit a hard 422
    ``quota_exceeded`` at ~98 MB with no advertised limit at all.
    """
    site = client.get("/site")
    data = site.get("data") or {}
    limit = data.get("internal_trial_assets_quota")
    used = site.get("data_usage")
    remaining = None
    if isinstance(limit, int) and isinstance(used, int):
        remaining = max(0, limit - used)
    return {"used_bytes": used, "limit_bytes": limit, "remaining_bytes": remaining}


def phase_plan(ctx: CloneContext) -> PhaseReport:
    """Read-only preflight: what exists, what it will cost, what cannot work."""
    report = PhaseReport("plan")
    src, tgt = ctx.source, ctx.target

    layouts = src.get_all("/layouts")
    layout_assets = src.get_all("/layout_assets")
    assets = src.get_all("/assets")
    pages = src.get_all("/pages")
    articles = src.get_all("/articles")
    languages = src.get_all("/languages")

    asset_bytes = sum(a.get("size") or 0 for a in assets if isinstance(a, dict))
    quota = quota_of(tgt)

    report.details = {
        "source": {
            "layouts": len(layouts),
            "layout_assets": len(layout_assets),
            "assets": len(assets),
            "asset_bytes": asset_bytes,
            "pages": len(pages),
            "articles": len(articles),
            "languages": [lang.get("code") for lang in languages],
        },
        "target_quota": quota,
        "known_limits": list(KNOWN_LIMITS),
    }

    remaining = quota.get("remaining_bytes")
    if remaining is None:
        report.notes.append(
            "Target's asset quota is not advertised by the API. The clone will "
            "upload until Voog refuses (422 quota_exceeded) and stop cleanly at "
            f"that point. Source media totals {asset_bytes / 1e6:.0f} MB."
        )
    elif asset_bytes > remaining:
        report.notes.append(
            f"Source media ({asset_bytes / 1e6:.0f} MB) exceeds the target's "
            f"remaining quota ({remaining / 1e6:.0f} MB). The assets phase will "
            "upload what fits, record what it skipped, and every phase that "
            "references a skipped image will report the gap rather than "
            "silently linking nothing."
        )
    else:
        report.notes.append(
            f"Source media ({asset_bytes / 1e6:.0f} MB) fits the target's "
            f"remaining quota ({remaining / 1e6:.0f} MB)."
        )

    # Duplicate paths: Voog no longer allows two articles on one path, so
    # the twins on a legacy source cannot be reproduced. Name them now,
    # while the operator is still deciding whether to run.
    seen: dict[str, int] = {}
    duplicates = []
    for article in articles:
        path = article.get("path")
        if not path:
            continue
        seen[path] = seen.get(path, 0) + 1
        if seen[path] == 2:
            duplicates.append(path)
    if duplicates:
        report.notes.append(
            f"{len(duplicates)} article path(s) appear more than once on the "
            "source (a legacy Voog allowance). The twins will land on "
            "auto-suffixed paths (-1) on the target: " + ", ".join(sorted(duplicates)[:5])
        )
    return report


# ================================================================== layouts


def _layout_key(layout: dict) -> tuple:
    return (layout.get("title"), bool(layout.get("component")))


def phase_layouts(ctx: CloneContext) -> PhaseReport:
    """Copy layouts and components, matching by (title, component).

    Title is the only stable identity across sites — ids are per-site and
    Voog assigns them. A target layout with a matching key is UPDATED in
    place rather than duplicated, so re-running never grows the target's
    layout list, and a site that was cloned once and edited since converges
    back to the source.
    """
    report = PhaseReport("layouts")
    src, tgt = ctx.source, ctx.target
    source_layouts = src.get_all("/layouts")
    target_layouts = tgt.get_all("/layouts")
    target_by_key = {_layout_key(layout): layout for layout in target_layouts}
    target_ids = {layout.get("id") for layout in target_layouts}

    for layout in source_layouts:
        source_id = layout.get("id")
        if ctx.state.has("layout_map", source_id):
            report.skipped += 1
            continue
        # Voog ships some layouts globally ("Blank layout"), so the same id
        # exists on both sites. Copying over one would overwrite a shared
        # system template; map it through untouched.
        if source_id in target_ids and _layout_key(layout) in target_by_key:
            shared = target_by_key[_layout_key(layout)]
            if shared.get("id") == source_id:
                ctx.state.put("layout_map", source_id, source_id)
                report.skipped += 1
                report.notes.append(f"shared system layout kept: {layout.get('title')!r}")
                continue

        # The list response carries the body only when Voog honours
        # include_body; fall back to the detail GET so an older deploy does
        # not silently copy empty layouts.
        body = layout.get("body")
        if body is None:
            body = (src.get(f"/layouts/{source_id}") or {}).get("body") or ""

        if ctx.dry_run:
            report.created += 1
            continue

        existing = target_by_key.get(_layout_key(layout))
        try:
            if existing:
                tgt.put(f"/layouts/{existing['id']}", {"body": body})
                ctx.state.put("layout_map", source_id, existing["id"])
                report.updated += 1
            else:
                created = tgt.post(
                    "/layouts",
                    {
                        "title": layout.get("title"),
                        "content_type": layout.get("content_type"),
                        "component": bool(layout.get("component")),
                        "body": body,
                    },
                )
                ctx.state.put("layout_map", source_id, created["id"])
                report.created += 1
        except Exception as exc:
            report.problem(f"layout {layout.get('title')!r}", describe_exc(exc))
    return report


# =========================================================== layout assets


def phase_layout_assets(ctx: CloneContext) -> PhaseReport:
    """Copy layout assets — text ones as ``data``, binaries as multipart.

    The split matters: ``layout_asset_create`` carries a UTF-8 ``data``
    string, which cannot represent a favicon or a font. Binaries go through
    ``POST /layout_assets`` as multipart, the route added in v1.4.3.

    A text asset's ``data`` lives ONLY on the detail endpoint — the list
    response dropped it in 2026-06 (see v1.4.1), which is what made an
    earlier `voog pull` write layouts-only manifests.
    """
    report = PhaseReport("layout_assets")
    src, tgt = ctx.source, ctx.target
    source_assets = src.get_all("/layout_assets")
    target_by_name = {a.get("filename"): a for a in tgt.get_all("/layout_assets")}

    for asset in source_assets:
        filename = asset.get("filename")
        if not filename:
            report.problem("layout_asset", f"entry without a filename: {asset.get('id')}")
            continue
        if ctx.state.has("layout_asset_map", filename):
            report.skipped += 1
            continue

        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        is_text = suffix in TEXT_ASSET_EXTENSIONS and asset.get("editable") is not False
        data = None
        if is_text:
            data = (src.get(f"/layout_assets/{asset['id']}") or {}).get("data")
            if data is None:
                # Voog says editable but serves no data — treat as binary
                # rather than pushing an empty file over a real one.
                is_text = False

        if ctx.dry_run:
            report.created += 1
            continue

        existing = target_by_name.get(filename)
        try:
            if is_text:
                if existing:
                    tgt.put(f"/layout_assets/{existing['id']}", {"data": data})
                    ctx.state.put("layout_asset_map", filename, existing["id"])
                    report.updated += 1
                else:
                    created = tgt.post("/layout_assets", {"filename": filename, "data": data})
                    ctx.state.put("layout_asset_map", filename, created.get("id"))
                    report.created += 1
            else:
                url = asset.get("public_url")
                if not url:
                    report.problem(filename, "binary asset has no public_url to copy from")
                    continue
                blob = _download(url)
                # filename is read-only on PUT, so a binary replacement is
                # delete-then-post — same workaround asset_replace uses.
                if existing:
                    tgt.delete(f"/layout_assets/{existing['id']}")
                created = tgt.post_file(
                    "/layout_assets",
                    filename=filename,
                    content=blob,
                    content_type=(
                        asset.get("content_type")
                        or mimetypes.guess_type(filename)[0]
                        or "application/octet-stream"
                    ),
                )
                ctx.state.put(
                    "layout_asset_map",
                    filename,
                    (created or {}).get("id") if isinstance(created, dict) else None,
                )
                report.created += 1
        except Exception as exc:
            report.problem(filename, describe_exc(exc))
    return report


# =================================================================== assets


def _upload_asset_bytes(tgt, *, filename: str, content_type: str, blob: bytes) -> dict:
    """Run Voog's 3-step upload for bytes already in memory.

    Deliberately not reusing ``products_images._upload_asset``: that one
    reads from a local path, and materialising 617 downloaded images as
    temp files to hand them back through a path API would be pure overhead.
    The protocol and the upload-URL validation are identical.
    """
    created = tgt.post(
        "/assets",
        {"filename": filename, "content_type": content_type, "size": len(blob)},
    )
    upload_url = created["upload_url"]
    _validate_upload_url(upload_url)
    request = urllib.request.Request(
        upload_url,
        data=blob,
        method="PUT",
        headers={"Content-Type": content_type, "x-amz-acl": "public-read"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"S3 upload failed: HTTP {response.status}")
    confirmed = tgt.put(f"/assets/{created['id']}/confirm")
    return confirmed if isinstance(confirmed, dict) else {"id": created["id"]}


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "quota_exceeded" in text or ("422" in text and "quota" in text)


def phase_assets(ctx: CloneContext) -> PhaseReport:
    """Copy the media library, newest-cheapest first, stopping at the quota.

    Uploads run in parallel because each is a download plus a multi-MB PUT,
    but the budget check is serialised through the state map: the worker
    reserves budget before it uploads, so a fan-out cannot collectively
    overshoot a quota that only one of them would have exceeded alone.
    """
    report = PhaseReport("assets")
    src, tgt = ctx.source, ctx.target
    source_assets = [a for a in src.get_all("/assets") if isinstance(a, dict) and a.get("id")]

    quota = quota_of(tgt)
    budget = ctx.asset_budget_bytes
    if budget is None:
        budget = quota.get("remaining_bytes")
    report.details["target_quota"] = quota

    todo = [a for a in source_assets if not ctx.state.has("asset_map", a["id"])]
    report.skipped = len(source_assets) - len(todo)

    # Smallest first. If the quota runs out we want to have copied the most
    # images, not the biggest ones — a missing hero photo is one visible
    # hole, a missing 400-image gallery is a broken site.
    todo.sort(key=lambda a: a.get("size") or 0)

    if ctx.dry_run:
        report.created = len(todo)
        report.details["would_upload_bytes"] = sum(a.get("size") or 0 for a in todo)
        return report

    ledger = _AssetBudget(budget)

    def _copy(asset: dict):
        if not ledger.reserve(asset.get("size") or 0):
            return "stopped"
        url = asset.get("public_url")
        if not url:
            raise RuntimeError("asset has no public_url")
        blob = _download(url)
        content_type = (
            asset.get("content_type")
            or mimetypes.guess_type(asset.get("filename") or "")[0]
            or "application/octet-stream"
        )
        confirmed = _upload_asset_bytes(
            tgt,
            filename=asset.get("filename") or f"asset-{asset['id']}",
            content_type=content_type,
            blob=blob,
        )
        ctx.state.put(
            "asset_map",
            asset["id"],
            {
                "id": confirmed.get("id"),
                "filename": confirmed.get("filename") or asset.get("filename"),
                "public_url": confirmed.get("public_url") or "",
            },
        )
        return "ok"

    results = parallel_map(propagate_tool_context(tgt, _copy), todo, max_workers=ctx.max_workers)
    stopped_count = 0
    for asset, outcome, exc in results:
        if exc is not None:
            if _is_quota_error(exc):
                # Voog said no. Stop the rest of the batch rather than
                # letting every remaining worker discover the same 422 and
                # strand an unconfirmed asset each time.
                ledger.stop()
                stopped_count += 1
                continue
            report.problem(asset.get("filename") or asset["id"], describe_exc(exc))
        elif outcome == "stopped":
            stopped_count += 1
        else:
            report.created += 1

    if stopped_count:
        report.notes.append(
            f"{stopped_count} asset(s) not uploaded — the target's asset quota "
            "ran out. Raise the plan (or free space) and re-run this phase; it "
            "resumes from the state map. Content phases will report every "
            "reference to an asset that is missing."
        )
        report.details["not_uploaded_due_to_quota"] = stopped_count
    report.details["uploaded_bytes"] = ledger.spent
    return report


# ===================================================================== site


def resolve_language_map(ctx: CloneContext, report: PhaseReport) -> dict:
    """Match source languages to target languages by ISO ``code``.

    Language ids are per-site, so every page and content area needs this
    bridge. It is a pure derivation from two read-only list calls, which is
    why it also runs during a dry run and why ``phase_pages`` can rebuild it
    on its own: a dry run that reported "no language mapping — run the site
    phase first" regardless of the real state told the operator nothing
    about whether the clone would work, and the dry run is this tool's
    headline safety feature. Persisted only on a real run.

    Languages are matched, never created: adding one changes every URL on a
    site, which is the operator's decision to make deliberately.
    """
    cached = ctx.state.load("language_map")
    if cached:
        return dict(cached)
    target_languages = {lang.get("code"): lang for lang in ctx.target.get_all("/languages")}
    mapping: dict = {}
    for lang in ctx.source.get_all("/languages"):
        code = lang.get("code")
        target_lang = target_languages.get(code)
        if not target_lang:
            report.problem(
                f"language {code!r}",
                "not present on the target — create it there first; the clone "
                "matches languages by code and never creates them (a new "
                "language changes every URL on the site).",
            )
            continue
        mapping[str(lang.get("id"))] = target_lang.get("id")
    if mapping and not ctx.dry_run:
        ctx.state.put_many("language_map", mapping)
    return mapping


def phase_site(ctx: CloneContext) -> PhaseReport:
    """Copy site settings, the site.data hash, and per-language titles.

    Settings go one field at a time: Voog rejects the whole body if any
    single field is unacceptable on the target's plan, and losing the other
    six because ``search_enabled`` needs a paid tier is a poor trade.
    """
    report = PhaseReport("site")
    src, tgt = ctx.source, ctx.target
    source_site = src.get("/site")

    # Runs in both modes: a dry run must be able to tell the operator that
    # the languages line up (or do not), which is the single most common
    # reason a clone cannot proceed.
    language_map = resolve_language_map(ctx, report)

    if ctx.dry_run:
        report.updated = 1
        report.details["language_map"] = language_map
        return report

    for field_name in _SITE_SETTINGS_FIELDS:
        value = source_site.get(field_name)
        if value is None:
            continue
        try:
            tgt.put("/site", {"site": {field_name: value}})
            report.updated += 1
        except Exception as exc:
            report.problem(f"site.{field_name}", describe_exc(exc))

    data = ctx.rewriter().data(source_site.get("data") or {}) or {}
    # Voog owns internal_* and rejects writes to them; they also describe the
    # SOURCE's plan and quota, which would be actively wrong on the target.
    data = {k: v for k, v in data.items() if not k.startswith(_INTERNAL_DATA_PREFIX)}
    if data:
        try:
            tgt.put("/site", {"site": {"data": data}})
            report.updated += 1
        except Exception as exc:
            report.problem("site.data", describe_exc(exc))

    # Language titles — the mapping itself was resolved (and persisted)
    # above; this only carries the display title across.
    target_by_id = {lang.get("id"): lang for lang in tgt.get_all("/languages")}
    for lang in src.get_all("/languages"):
        target_lang = target_by_id.get(language_map.get(str(lang.get("id"))))
        if not target_lang:
            continue
        if lang.get("title") and lang.get("title") != target_lang.get("title"):
            try:
                tgt.put(f"/languages/{target_lang['id']}", {"title": lang["title"]})
                report.updated += 1
            except Exception as exc:
                report.problem(f"language {lang.get('code')!r} title", describe_exc(exc))
    return report


# ==================================================================== pages


def _fallback_layout_id(target_layouts: list, content_type: str | None) -> int | None:
    """Pick a target layout a page of this content_type can legally use.

    Prefers an exact ``content_type`` match (a blog page needs a `blog`
    layout, not a `page` one), then any non-component layout. Components are
    excluded — they are fragments, not page templates.
    """
    usable = [
        layout
        for layout in target_layouts
        if isinstance(layout, dict) and layout.get("id") and not layout.get("component")
    ]
    wanted = content_type or "page"
    for layout in usable:
        if layout.get("content_type") == wanted:
            return layout["id"]
    for layout in usable:
        if layout.get("content_type") == "page":
            return layout["id"]
    return usable[0]["id"] if usable else None


def phase_pages(ctx: CloneContext) -> PhaseReport:
    """Create or repurpose target pages, then restore the menu order.

    Pages are matched by ``path`` — the one field that is both stable and
    meaningful across sites, and the thing that breaks visibly if it drifts
    (every inbound link and every internal href is a path). A target page
    on the same path is UPDATED, which is what makes a clone into a
    partially-built site converge instead of doubling it.
    """
    report = PhaseReport("pages")
    src, tgt = ctx.source, ctx.target
    layout_map = ctx.state.load("layout_map")
    # Rebuild the language mapping if this phase is being run on its own
    # (`--phases pages`, or a resume). It is a read-only derivation, so
    # depending on an earlier phase having persisted it would make a
    # legitimate single-phase run fail for no reason.
    language_map = resolve_language_map(ctx, report)

    source_pages = src.get_all("/pages")
    target_by_path = {(p.get("path") or "").strip("/"): p for p in tgt.get_all("/pages")}
    target_layouts = tgt.get_all("/layouts")

    # Page hierarchy is expressed through NODES, not a `parent` field on the
    # page: a child's `node.parent_id` names its parent's NODE, while
    # `POST /pages` wants the parent's PAGE id. Bridge the two here, or every
    # copied page lands at the root and the menu comes out flat.
    page_id_by_node = {
        (p.get("node") or {}).get("id"): p.get("id")
        for p in source_pages
        if isinstance(p.get("node"), dict)
    }

    if not language_map:
        # resolve_language_map already recorded which codes are missing.
        report.problem(
            "languages",
            "no source language matches a target language by code, so no page "
            "can be created (a page needs a target language_id). Create the "
            "language on the target first.",
        )
        return report

    for page in sorted(
        source_pages, key=lambda p: len((p.get("path") or "").strip("/").split("/"))
    ):
        source_id = page.get("id")
        if ctx.state.has("page_map", source_id):
            report.skipped += 1
            continue
        path = (page.get("path") or "").strip("/")
        source_layout = page.get("layout")
        source_layout_id = (
            source_layout.get("id") if isinstance(source_layout, dict) else source_layout
        )
        body = {
            "title": page.get("title"),
            "hidden": bool(page.get("hidden")),
            "description": page.get("description"),
            "keywords": page.get("keywords"),
            "menu_title": page.get("menu_title"),
            "data": ctx.rewriter().data(page.get("data") or {}),
            "publishing": True,
        }
        if page.get("slug"):
            body["slug"] = page["slug"]
        # A page MUST carry a layout_id Voog considers available: POST
        # /pages with none (or with an id from the source site) is rejected
        # with 422 `{"errors":{"layout_id":["not in available layouts
        # list"]}}` — confirmed live on kolm-koma-2026 2026-08-13. Voog does
        # not pick a default, so the clone has to.
        mapped_layout = layout_map.get(str(source_layout_id))
        if mapped_layout:
            body["layout_id"] = mapped_layout
        else:
            fallback = _fallback_layout_id(target_layouts, page.get("content_type"))
            if fallback:
                body["layout_id"] = fallback
                report.problem(
                    f"page {path or '/'}",
                    (
                        f"source layout {source_layout_id} is not in layout_map "
                        "(run the `layouts` phase first) — used the target's own "
                        f"{page.get('content_type') or 'page'} layout {fallback} instead"
                    )
                    if source_layout_id
                    else (
                        "the source page names no layout — used the target's own "
                        f"{page.get('content_type') or 'page'} layout {fallback}"
                    ),
                )
            else:
                report.problem(
                    f"page {path or '/'}",
                    "no usable layout on the target for content_type "
                    f"{page.get('content_type') or 'page'!r}, and Voog rejects a "
                    "page without one — run the `layouts` phase first. Page skipped.",
                )
                continue
        body = {k: v for k, v in body.items() if v is not None}

        if ctx.dry_run:
            if path in target_by_path:
                report.updated += 1
            else:
                report.created += 1
            continue

        existing = target_by_path.get(path)
        try:
            if existing:
                tgt.put(f"/pages/{existing['id']}", body)
                ctx.state.put("page_map", source_id, existing["id"])
                report.updated += 1
            else:
                source_lang = page.get("language") or {}
                source_lang_id = (
                    source_lang.get("id") if isinstance(source_lang, dict) else source_lang
                )
                target_lang_id = language_map.get(str(source_lang_id)) or next(
                    iter(language_map.values())
                )
                body["language_id"] = target_lang_id
                body["content_type"] = page.get("content_type") or "page"
                parent_node_id = (page.get("node") or {}).get("parent_id")
                parent_source_page = page_id_by_node.get(parent_node_id)
                parent_target_page = ctx.state.get("page_map", parent_source_page)
                if parent_target_page:
                    body["parent_id"] = parent_target_page
                elif parent_node_id:
                    # Sorted shallowest-first above, so the parent should
                    # already exist. If it does not, the page still gets
                    # created — at the root — and that is worth saying.
                    report.problem(
                        f"page {path or '/'}",
                        f"parent page for node {parent_node_id} is not in page_map; "
                        "created at the root instead of under its parent",
                    )
                created = tgt.post("/pages", body)
                ctx.state.put("page_map", source_id, created["id"])
                report.created += 1
        except Exception as exc:
            report.problem(f"page {path or '/'}", describe_exc(exc))

    if not ctx.dry_run:
        report.details["menu_order"] = _restore_menu_order(ctx, source_pages, report)
    return report


def _restore_menu_order(ctx: CloneContext, source_pages: list, report: PhaseReport) -> dict:
    """Reposition target nodes so the menu reads in the source's order.

    Menu order is the most visible thing a clone gets wrong and the least
    visible in an API diff: the pages are all there, with the right titles
    and paths, in the wrong sequence.

    ``PUT /nodes/{id}/move`` takes QUERY-STRING params, not a body — the
    same shape as ``node_move`` and ``element_move``. Positions are
    reassigned densely from 1 within each parent, because the source's own
    numbering can have gaps (deleted siblings) that Voog would reject.
    """
    tgt = ctx.target
    page_map = ctx.state.load("page_map")
    moved, failed = 0, 0

    # Group source pages by parent node, ordered by their source position.
    siblings: dict = {}
    for page in source_pages:
        node = page.get("node")
        if not isinstance(node, dict) or not node.get("id"):
            continue
        siblings.setdefault(node.get("parent_id"), []).append(
            (node.get("position") or 0, page.get("id"))
        )

    for parent_node_id, entries in siblings.items():
        if parent_node_id is None:
            # Root level: a single node with nothing to be ordered against.
            continue
        for position, (_source_position, source_page_id) in enumerate(sorted(entries), start=1):
            target_page_id = page_map.get(str(source_page_id))
            if not target_page_id:
                continue
            try:
                target_node = (tgt.get(f"/pages/{target_page_id}").get("node") or {}) or {}
                node_id, target_parent = target_node.get("id"), target_node.get("parent_id")
                if not node_id or not target_parent:
                    continue
                tgt.put(
                    f"/nodes/{node_id}/move",
                    params={"parent_id": target_parent, "position": position},
                )
                moved += 1
            except Exception as exc:
                failed += 1
                report.problem(f"menu position for source page {source_page_id}", describe_exc(exc))
    return {"moved": moved, "failed": failed}


# ================================================================= contents


def _fill_gallery(tgt, gallery_id: int, entries: list):
    """Populate one media_set, verifying the order actually took.

    A module-level function rather than a closure so the media_set id and
    the entry list are bound as arguments — a lambda over loop variables
    reads the LAST iteration's values if it is ever called after the loop
    moves on, which is one refactor away from silently writing every gallery
    with the same images.
    """

    def _read_order() -> list:
        current = (tgt.get(f"/media_sets/{gallery_id}") or {}).get("assets") or []
        ordered = sorted(
            (a for a in current if isinstance(a, dict)),
            key=lambda a: a.get("position") or 0,
        )
        return [a.get("id") for a in ordered]

    return put_ordered_with_readback(
        put=lambda: tgt.put(f"/media_sets/{gallery_id}", {"assets": entries}),
        read_order=_read_order,
        wanted=[e["id"] for e in entries],
    )


def _sync_contents(
    ctx: CloneContext,
    *,
    kind: str,
    target_parent_id: int,
    source_contents: list,
    report: PhaseReport,
) -> int:
    """Rebuild one parent's content areas: text bodies and galleries.

    Replace rather than merge. Voog assigns content ids per site, so there
    is no stable correspondence to update against; deleting and recreating
    is the only operation with a predictable result. That is destructive on
    the target parent by design — it is what "clone this page" means.
    """
    tgt = ctx.target
    asset_map = ctx.state.load("asset_map")
    created = 0

    try:
        existing = tgt.get(f"/{kind}/{target_parent_id}/contents")
    except Exception as exc:
        report.problem(f"{kind}/{target_parent_id} contents", f"cannot read: {describe_exc(exc)}")
        return 0
    for content in existing if isinstance(existing, list) else []:
        try:
            tgt.delete(f"/{kind}/{target_parent_id}/contents/{content['id']}")
        except Exception as exc:
            report.problem(
                f"{kind}/{target_parent_id} content {content.get('name')!r}",
                f"cannot delete before rebuild: {describe_exc(exc)}",
            )

    ordered = sorted(
        [c for c in source_contents if isinstance(c, dict)],
        key=lambda c: (c.get("position") or 0, c.get("name") or ""),
    )
    for content in ordered:
        name = content.get("name")
        content_type = content.get("content_type")
        try:
            if content_type == "text":
                made = tgt.post(
                    f"/{kind}/{target_parent_id}/contents",
                    {"name": name, "content_type": "text"},
                )
                text_id = (made.get("text") or {}).get("id")
                source_text_id = (content.get("text") or {}).get("id")
                body = ""
                if source_text_id:
                    body = (ctx.source.get(f"/texts/{source_text_id}") or {}).get("body") or ""
                if text_id:
                    tgt.put(f"/texts/{text_id}", {"body": ctx.rewriter().text(body)})
                    created += 1
                else:
                    report.problem(
                        f"{kind}/{target_parent_id} text {name!r}",
                        "Voog created the content area without a text id",
                    )
            elif content_type == "gallery":
                made = tgt.post(
                    f"/{kind}/{target_parent_id}/contents",
                    {"name": name, "content_type": "gallery"},
                )
                gallery_id = (made.get("gallery") or {}).get("id")
                if not gallery_id:
                    fetched = tgt.get(f"/{kind}/{target_parent_id}/contents/{made['id']}")
                    gallery_id = ((fetched or {}).get("gallery") or {}).get("id")
                if not gallery_id:
                    report.problem(
                        f"{kind}/{target_parent_id} gallery {name!r}",
                        "no media_set id on the new content area",
                    )
                    continue
                source_gallery = content.get("gallery") or {}
                entries, missing = [], 0
                source_assets = sorted(
                    source_gallery.get("assets") or [],
                    key=lambda a: a.get("position") or 0,
                )
                for asset in source_assets:
                    mapped = asset_map.get(str(asset.get("id")))
                    if not mapped:
                        missing += 1
                        continue
                    entries.append(
                        {
                            "id": mapped["id"],
                            # Explicit 1-based position: media_sets do NOT
                            # take order from the array (v1.4.4). Renumbered
                            # over the assets that survived the quota, so a
                            # skipped image does not leave a hole Voog has to
                            # interpret.
                            "position": len(entries) + 1,
                            "title": asset.get("title") or "",
                            "settings": asset.get("settings") or {},
                        }
                    )
                if missing:
                    report.problem(
                        f"{kind}/{target_parent_id} gallery {name!r}",
                        f"{missing} of {len(source_assets)} image(s) are not in "
                        "asset_map (not uploaded — most likely the quota); the "
                        "gallery is built from the rest",
                    )
                if entries:
                    outcome = _fill_gallery(tgt, gallery_id, entries)
                    wanted_ids = [e["id"] for e in entries]
                    if outcome.error is not None:
                        report.problem(
                            f"{kind}/{target_parent_id} gallery {name!r}",
                            (
                                f"gallery WAS written ({outcome.writes_applied} of "
                                f"{outcome.attempts} attempts) and then a write failed: "
                                f"{outcome.error}"
                            )
                            if outcome.target_modified
                            else f"gallery could not be filled, no write applied: {outcome.error}",
                        )
                    elif outcome.read_failed:
                        report.problem(
                            f"{kind}/{target_parent_id} gallery {name!r}",
                            "written, but reading it back to confirm FAILED — neither "
                            "the images nor their order is verified",
                        )
                    elif not outcome.verified:
                        report.problem(
                            f"{kind}/{target_parent_id} gallery {name!r}",
                            f"images ARE linked but Voog did not apply the order after "
                            f"{outcome.attempts} attempts (wanted {wanted_ids}, "
                            f"holds {outcome.final})",
                        )
                created += 1
            else:
                report.problem(
                    f"{kind}/{target_parent_id} content {name!r}",
                    f"unsupported content_type {content_type!r} — not copied",
                )
        except Exception as exc:
            report.problem(f"{kind}/{target_parent_id} content {name!r}", describe_exc(exc))
    return created


def phase_contents(ctx: CloneContext) -> PhaseReport:
    """Rebuild every page's content areas, plus the language-level ones."""
    report = PhaseReport("contents")
    page_map = ctx.state.load("page_map")
    language_map = ctx.state.load("language_map")

    if not page_map:
        report.problem("pages", "page_map is empty — run the `pages` phase first")
        return report

    for source_page_id, target_page_id in sorted(page_map.items(), key=lambda kv: int(kv[0])):
        if ctx.state.has("contents_done", source_page_id):
            report.skipped += 1
            continue
        try:
            source_contents = ctx.source.get(f"/pages/{source_page_id}/contents")
        except Exception as exc:
            report.problem(
                f"page {source_page_id} contents", f"cannot read source: {describe_exc(exc)}"
            )
            continue
        if ctx.dry_run:
            report.created += len(source_contents or [])
            continue
        made = _sync_contents(
            ctx,
            kind="pages",
            target_parent_id=target_page_id,
            source_contents=source_contents or [],
            report=report,
        )
        report.created += made
        ctx.state.put("contents_done", source_page_id, {"target": target_page_id, "areas": made})

    # Language-level contents (footers and other site-wide areas).
    for source_lang_id, target_lang_id in language_map.items():
        key = f"lang:{source_lang_id}"
        if ctx.state.has("contents_done", key):
            report.skipped += 1
            continue
        try:
            source_contents = ctx.source.get(f"/languages/{source_lang_id}/contents")
        except Exception as exc:
            report.problem(
                f"language {source_lang_id} contents", f"cannot read source: {describe_exc(exc)}"
            )
            continue
        if ctx.dry_run:
            report.created += len(source_contents or [])
            continue
        made = _sync_contents(
            ctx,
            kind="languages",
            target_parent_id=target_lang_id,
            source_contents=source_contents or [],
            report=report,
        )
        report.created += made
        ctx.state.put("contents_done", key, {"target": target_lang_id, "areas": made})
    return report


# ================================================================= articles


def phase_articles(ctx: CloneContext) -> PhaseReport:
    """Copy articles: body, data, tags, cover image, and per-article contents.

    Articles are created oldest-first so their relative order on the target
    matches the source as closely as Voog allows. It does not allow much —
    ``created_at`` is not settable (PUT returns 200 and resets it to now),
    so every copied article carries the clone date and any layout that
    sorts by date will show them in creation order, not the original one.
    """
    report = PhaseReport("articles")
    src, tgt = ctx.source, ctx.target
    page_map = ctx.state.load("page_map")
    asset_map = ctx.state.load("asset_map")

    source_articles = sorted(src.get_all("/articles"), key=lambda a: a.get("created_at") or "")
    if not source_articles:
        return report

    for article in source_articles:
        source_id = article.get("id")
        if ctx.state.has("article_map", source_id):
            report.skipped += 1
            continue
        try:
            detail = src.get(f"/articles/{source_id}")
        except Exception as exc:
            report.problem(f"article {source_id}", f"cannot read source: {describe_exc(exc)}")
            continue

        source_page = detail.get("page") or {}
        source_page_id = source_page.get("id") if isinstance(source_page, dict) else source_page
        target_page_id = page_map.get(str(source_page_id))
        if not target_page_id:
            report.problem(
                f"article {detail.get('title')!r}",
                f"its blog page ({source_page_id}) is not in page_map — run the "
                "`pages` phase first",
            )
            continue

        if ctx.dry_run:
            report.created += 1
            continue

        try:
            created = tgt.post(
                "/articles",
                {
                    "page_id": target_page_id,
                    "autosaved_title": detail.get("title"),
                    "path": detail.get("path"),
                    "data": ctx.rewriter().data(detail.get("data") or {}),
                    "tag_names": detail.get("tag_names") or [],
                    **({"description": detail["description"]} if detail.get("description") else {}),
                },
            )
        except Exception as exc:
            report.problem(
                f"article {detail.get('title')!r}", f"create failed: {describe_exc(exc)}"
            )
            continue
        target_id = created.get("id")
        ctx.state.put("article_map", source_id, target_id)
        report.created += 1

        if created.get("path") and detail.get("path") and created["path"] != detail["path"]:
            report.problem(
                f"article {detail.get('title')!r}",
                f"path {detail['path']!r} was taken; Voog assigned "
                f"{created['path']!r}. Duplicate source paths cannot be "
                "reproduced — see the clone's known limits.",
            )

        # Publish state: a source draft must stay a draft on the target, or
        # the clone publishes unfinished work. The field is `published`
        # (confirmed live 2026-08-13 — there is no `public` or `status` on
        # an article); `publishing: true` is what promotes the autosaved_*
        # values to the live ones.
        publish_body = {
            "autosaved_title": detail.get("title"),
            "autosaved_body": ctx.rewriter().text(
                detail.get("body") or detail.get("autosaved_body") or ""
            ),
            "autosaved_excerpt": detail.get("excerpt") or detail.get("autosaved_excerpt") or "",
            "publishing": bool(detail.get("published")),
        }
        try:
            tgt.put(f"/articles/{target_id}", publish_body)
        except Exception as exc:
            report.problem(
                f"article {detail.get('title')!r}", f"publish failed: {describe_exc(exc)}"
            )

        cover = detail.get("image")
        if isinstance(cover, dict) and cover.get("id"):
            mapped = asset_map.get(str(cover["id"]))
            if mapped:
                try:
                    tgt.put(f"/articles/{target_id}", {"image_id": mapped["id"]})
                except Exception as exc:
                    report.problem(f"article {detail.get('title')!r} cover", describe_exc(exc))
            else:
                report.problem(
                    f"article {detail.get('title')!r} cover",
                    "cover image is not in asset_map (not uploaded — most likely "
                    "the quota); the article has no cover on the target",
                )

        try:
            source_contents = src.get(f"/articles/{source_id}/contents")
        except Exception as exc:
            report.problem(
                f"article {source_id} contents", f"cannot read source: {describe_exc(exc)}"
            )
            continue
        _sync_contents(
            ctx,
            kind="articles",
            target_parent_id=target_id,
            source_contents=source_contents or [],
            report=report,
        )

    report.notes.append(
        "created_at and published_at are not settable via the API — every copied "
        "article carries the clone date."
    )
    return report


# ================================================================== cleanup


def phase_cleanup(ctx: CloneContext) -> PhaseReport:
    """Delete target-only layouts and layout assets left over from before.

    Only touches objects the SOURCE does not have and the target is not
    using: a layout still assigned to a page is kept and reported, because
    deleting it would break a live page to tidy up a list.
    """
    report = PhaseReport("cleanup")
    tgt = ctx.target
    source_layout_keys = {_layout_key(layout) for layout in ctx.source.get_all("/layouts")}
    source_asset_names = {a.get("filename") for a in ctx.source.get_all("/layout_assets")}
    mapped_layout_ids = {int(v) for v in ctx.state.load("layout_map").values() if v}

    in_use = set()
    for page in tgt.get_all("/pages"):
        layout = page.get("layout")
        if isinstance(layout, dict) and layout.get("id"):
            in_use.add(layout["id"])

    for layout in tgt.get_all("/layouts"):
        if _layout_key(layout) in source_layout_keys or layout.get("id") in mapped_layout_ids:
            continue
        if layout.get("id") in in_use:
            report.skipped += 1
            report.notes.append(f"kept in-use layout {layout.get('title')!r}")
            continue
        if ctx.dry_run:
            report.updated += 1
            continue
        try:
            tgt.delete(f"/layouts/{layout['id']}")
            report.updated += 1
        except Exception as exc:
            report.problem(f"layout {layout.get('title')!r}", describe_exc(exc))

    for asset in tgt.get_all("/layout_assets"):
        if asset.get("filename") in source_asset_names:
            continue
        if ctx.dry_run:
            report.updated += 1
            continue
        try:
            tgt.delete(f"/layout_assets/{asset['id']}")
            report.updated += 1
        except Exception as exc:
            report.problem(f"layout_asset {asset.get('filename')!r}", describe_exc(exc))
    return report


# =================================================================== verify


def phase_verify(ctx: CloneContext) -> PhaseReport:
    """Compare the two sites: object counts, then rendered HTML per page.

    The HTML comparison is the one that catches real breakage — counts can
    match while every image 404s. Sizes are reported rather than judged: a
    clone always differs by at least the domain name, and on a trial site
    by Voog's own banner too.
    """
    report = PhaseReport("verify")
    src, tgt = ctx.source, ctx.target

    counts = {}
    for label, endpoint in (
        ("layouts", "/layouts"),
        ("layout_assets", "/layout_assets"),
        ("assets", "/assets"),
        ("pages", "/pages"),
        ("articles", "/articles"),
    ):
        try:
            counts[label] = {
                "source": len(src.get_all(endpoint)),
                "target": len(tgt.get_all(endpoint)),
            }
        except Exception as exc:
            report.problem(f"count {label}", describe_exc(exc))
    report.details["counts"] = counts
    for label, pair in counts.items():
        if pair["source"] != pair["target"]:
            report.notes.append(
                f"{label}: source {pair['source']}, target {pair['target']} "
                f"(delta {pair['target'] - pair['source']:+d})"
            )

    source_host = getattr(src, "host", None)
    target_host = getattr(tgt, "host", None)
    pages = []
    for page in src.get_all("/pages"):
        if page.get("hidden"):
            continue
        pages.append("/" + (page.get("path") or "").strip("/"))
    rendered = []
    for path in pages[:10]:
        row = {"path": path}
        for label, host in (("source", source_host), ("target", target_host)):
            if not host:
                continue
            try:
                html = _download(f"https://{host}{path}", timeout=60)
                row[f"{label}_bytes"] = len(html)
            except (urllib.error.URLError, OSError) as exc:
                row[f"{label}_error"] = str(exc)
        if "source_bytes" in row and "target_bytes" in row:
            row["delta_bytes"] = row["target_bytes"] - row["source_bytes"]
        rendered.append(row)
    report.details["rendered"] = rendered
    report.notes.append(
        "A non-zero delta is expected: the domain name differs on every page, "
        "and a trial site also carries Voog's banner. Large or wildly varying "
        "deltas are the signal worth chasing."
    )
    return report


PHASE_FUNCTIONS = {
    "plan": phase_plan,
    "layouts": phase_layouts,
    "layout_assets": phase_layout_assets,
    "assets": phase_assets,
    "site": phase_site,
    "pages": phase_pages,
    "contents": phase_contents,
    "articles": phase_articles,
    "cleanup": phase_cleanup,
    "verify": phase_verify,
}


__all__ = [
    "KNOWN_LIMITS",
    "MUTATING_PHASES",
    "PHASE_FUNCTIONS",
    "PHASE_ORDER",
    "CloneContext",
    "PhaseReport",
    "quota_of",
]
