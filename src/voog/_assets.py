"""Shared helpers for Voog's asset library: lookup and derivative waiting.

The 3-step upload protocol itself lives in
:mod:`voog.mcp.tools.products_images` (``_upload_asset``); this module holds
the two things every caller around it needs and that cost real money to
learn the hard way:

  - :func:`find_asset_by_filename` — Voog auto-suffixes a duplicate filename
    (``photo-1.jpg``), so re-uploading the same name silently creates a second
    asset and leaves the original unreferenced. Look before you upload.
  - :func:`wait_for_derivatives` — Voog builds resized copies asynchronously,
    and a derivative URL requested before it exists answers 403 from behind
    the CDN, which then *caches the 403*. Ask the API, never the CDN.

Both were learned in the stellasoomlais-voog `scripts/men-images.py`
maintenance script, which needed three successive corrections before it got
the derivative handling right (commits "never request a derivative before
Voog has made it", "correct the cached-403 lifetime", "trust the derivative
check less"). Encoding them here means the next caller does not repeat that.
"""

from __future__ import annotations

import time
import urllib.parse

# Voog caps each derivative's HEIGHT; a resize is produced only when the
# original is taller than the cap. Widths therefore depend on the source
# aspect ratio and cannot be predicted — which is why callers must read the
# `sizes` array rather than assume a width list (a guessed width in a srcset
# 403s, and browsers do not fall back to another candidate — the image just
# renders blank).
DERIVATIVE_HEIGHT_CAPS = (150, 600, 1280, 2048)


def expected_derivative_count(height: int | None) -> int:
    """How many resized copies Voog should make for an original this tall."""
    if not height:
        return 0
    return sum(1 for cap in DERIVATIVE_HEIGHT_CAPS if height > cap)


def is_asset_complete(asset) -> bool:
    """True when Voog has reported dimensions AND every expected resize.

    Lets callers skip the poll entirely for an asset that is already
    finished — a `status: done` asset found by lookup normally is.
    """
    if not isinstance(asset, dict):
        return False
    height = asset.get("height")
    if not height:
        return False
    return len(asset.get("sizes") or []) >= expected_derivative_count(height)


def find_asset_by_filename(client, filename: str) -> dict | None:
    """Return the finished asset with exactly this filename, or None.

    Uses ``?q.asset.filename.$eq=`` — the ``$match`` / prefix forms are
    ignored by Voog and come back as the full library, which reads as "no
    match" to a careless caller. Only ``status == "done"`` counts: an asset
    that was created but never confirmed cannot be linked to anything.
    """
    quoted = urllib.parse.quote(filename)
    result = client.get(f"/assets?q.asset.filename.$eq={quoted}")
    if not isinstance(result, list):
        return None
    for asset in result:
        if asset.get("filename") == filename and asset.get("status") == "done":
            return asset
    return None


def wait_for_derivatives(
    client,
    asset_id: int,
    *,
    timeout_s: float = 120.0,
    poll_s: float = 5.0,
    sleep=time.sleep,
) -> dict:
    """Poll ``GET /assets/{id}`` until Voog reports dimensions + derivatives.

    Returns the asset record — complete if the derivatives arrived in time,
    otherwise the last one seen (callers surface a partial ``sizes`` rather
    than failing: the upload itself succeeded and re-reading later fills it
    in).

    NEVER probe a derivative URL over HTTP to decide whether it exists. Voog
    builds them asynchronously behind a CDN that answers 403 for one that is
    not there yet — and that 403 gets cached for roughly an hour, clearing
    edge by edge, so a single early HEAD pins a failure onto a URL that
    became valid seconds later. (Do not read the branded host's seven-day
    ``max-age`` as the error's TTL — that governs successful responses.)
    Recovery costs an hour of waiting or a re-upload under a new filename.
    The asset's own ``sizes`` array is API state with no CDN in front of it,
    so that is the authority this function polls.
    """
    deadline = timeout_s
    asset = client.get(f"/assets/{asset_id}")
    while not is_asset_complete(asset):
        if deadline <= 0:
            return asset
        sleep(min(poll_s, deadline))
        deadline -= poll_s
        asset = client.get(f"/assets/{asset_id}")
    return asset


def summarize_asset(asset: dict) -> dict:
    """Curated view: what a caller needs to build markup, nothing else."""
    sizes = []
    for size in asset.get("sizes") or []:
        if size.get("filename"):
            sizes.append(
                {
                    "width": size.get("width"),
                    "height": size.get("height"),
                    "filename": size["filename"],
                    "path": f"/photos/{size['filename']}",
                }
            )
    filename = asset.get("filename") or ""
    return {
        "id": asset.get("id"),
        "filename": filename,
        "status": asset.get("status"),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "path": f"/photos/{filename}" if filename else "",
        "sizes": sorted(sizes, key=lambda s: s.get("width") or 0),
    }
