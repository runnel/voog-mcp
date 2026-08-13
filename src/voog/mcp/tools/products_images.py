"""MCP tool for replacing a product's images via the 3-step Voog asset
upload protocol.

One tool: ``product_set_images``. Reads local image files from disk, uploads
each via Voog's 3-step protocol, then PUTs the resulting asset references to
the product.

The 3-step protocol per file (matches ``voog.py``'s ``upload_asset``):

  1. POST ``/assets`` (admin/api) → returns ``{id, upload_url}``
  2. PUT to ``upload_url`` with raw binary body — uses ``urllib.request``
     directly, NOT ``VoogClient`` (S3 endpoint, not JSON, not Voog-auth'd)
  3. PUT ``/assets/{id}/confirm`` (admin/api) → asset becomes usable

Then a final PUT ``/products/{id}`` (ecommerce_url) with flat
``{image_id, assets:[{id:n}]}`` payload — note: NOT wrapped in
``{product: {...}}`` unlike :func:`voog.mcp.tools.products._product_update`.
Voog's wrapper convention varies per operation, not per resource. **Critical
PUT-vs-POST gotcha** (see ``feedback_voog_assets_vs_asset_ids`` memory):
the field is ``assets:[{id:n}]`` on PUT, not ``asset_ids`` (which is
POST-only). Sending ``asset_ids`` on PUT silently keeps only the first/hero
image — the gallery images vanish without an error.

**Partial failure semantics (collect-then-decide):** uploads run in parallel
(:func:`voog._concurrency.parallel_map`, ``max_workers=3`` per spec § 4.3 —
each upload is a multi-MB binary, so the cap is conservative). All uploads
run to completion *before* the product PUT decision; if any single upload
fails, the final product PUT is skipped — better to surface the failure
cleanly than leave the product with a half-set of images. Successful uploads
remain in Voog's asset library as orphans (up to ``len(files) - 1`` of them
if a single upload fails, vs. 0–N-1 under the old first-failure-break loop).
Surfaced in ``uploaded`` for the caller to re-link via admin UI, re-run the
tool with the failed file(s) removed, or DELETE ``/assets/{id}`` to clean up.

**Why this lives in its own module:** the 3-step protocol is markedly more
complex than the rest of :mod:`voog.mcp.tools.products` (list/get/update),
and isolating it keeps that module's read+translate flow easy to follow.
"""

import time
import urllib.request
from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from voog._concurrency import parallel_map, propagate_tool_context
from voog._ordering import put_ordered_with_readback
from voog._upload_validation import _validate_upload_url
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_int, strip_site

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="product_set_images",
            description=(
                "Replace a product's images. `files` is a list of absolute "
                "paths to local image files (jpg, jpeg, png, webp, gif). "
                "First file becomes the main image (image_id); rest are "
                "gallery images. Runs Voog's 3-step asset upload protocol "
                "per file (POST /assets → PUT upload_url → PUT confirm), "
                "then PUTs {image_id, assets:[{id:n}]} to /products/{id}. "
                "Refuses to replace existing images unless force=true. "
                "If any single upload fails, the product is NOT updated — "
                "successful uploads are surfaced in `uploaded` for manual "
                "re-linking.\n"
                "\n"
                "Gallery ORDER is applied by re-reading it back and repeating "
                "the PUT: Voog lands the requested order only about half the "
                "time on the first write (200 either way). If the order still "
                "has not taken after 3 attempts the result says so and carries "
                "`order_verified: false` plus the `stored_asset_ids` Voog "
                "actually holds — every image is linked in that case, only the "
                "sequence is wrong."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "product_id": {
                        "type": "integer",
                        "description": "Voog ecommerce product id",
                    },
                    "files": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                        "description": (
                            "Absolute paths to local image files. First is "
                            "the main image, rest are gallery."
                        ),
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Required to replace existing images. Defensive "
                            "opt-in like page_delete — even with the "
                            "destructiveHint annotation, the server refuses "
                            "without force=true."
                        ),
                    },
                },
                "required": ["site", "product_id", "files"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "product_set_images":
        # S9: tag every HTTP request inside this handler with X-MCP-Tool +
        # shared X-Request-Id. The parallel asset uploads inside
        # _product_set_images use propagate_tool_context for thread
        # propagation. See VoogClient.with_tool docstring.
        with client.with_tool(name):
            return _product_set_images(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _product_set_images(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    files = arguments.get("files") or []
    force = bool(arguments.get("force", False))

    err = require_int("product_id", product_id, tool_name="product_set_images")
    if err:
        return error_response(err)
    if not files:
        return error_response(
            "product_set_images: files must be a non-empty array of absolute paths"
        )

    # Validate every path before any API call — fail fast on caller mistakes.
    paths: list[Path] = []
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            return error_response(f"product_set_images: path must be absolute (got {f!r})")
        if not p.exists():
            return error_response(f"product_set_images: file not found: {f}")
        ext = p.suffix.lower()
        if ext not in CONTENT_TYPES:
            return error_response(
                f"product_set_images: unsupported extension {ext!r}. "
                f"Allowed: {', '.join(sorted(CONTENT_TYPES))}"
            )
        paths.append(p)

    # Pre-flight: fetch current product to surface old asset_ids and gate
    # the force=false path.
    try:
        product = client.get(
            f"/products/{product_id}",
            base=client.ecommerce_url,
        )
    except Exception as e:
        return error_response(f"product_set_images: cannot fetch product {product_id}: {e}")
    old_asset_ids = product.get("asset_ids") or []

    if old_asset_ids and not force:
        return error_response(
            f"product_set_images: product {product_id} has {len(old_asset_ids)} "
            f"existing image(s). Pass force=true to replace them."
        )

    # 3-step upload per file, in parallel (collect-then-decide per spec § 4.6).
    # All uploads run to completion before deciding on the product PUT — the
    # old first-failure-break loop is gone, so up to len(paths)-1 successful
    # uploads can be left as orphans in Voog's asset library if any upload
    # fails. max_workers=3: each upload is a multi-MB binary; 3 in flight
    # caps net I/O at ~15 MB for typical 5 MB images (spec § 4.3).
    uploaded: list[dict] = []
    failed: list[dict] = []
    # S9d: wrap with propagate_tool_context so each upload worker thread
    # carries the with_tool scope's X-MCP-Tool: product_set_images +
    # X-Request-Id headers on its 3-step upload requests (asset POST →
    # presigned PUT → confirm PUT).
    upload_results = parallel_map(
        propagate_tool_context(client, lambda p: _upload_asset(p, client)),
        paths,
        max_workers=3,
    )
    for path, asset, exc in upload_results:
        if exc is not None:
            failed.append({"filename": path.name, "error": str(exc)})
        else:
            uploaded.append(
                {
                    "filename": path.name,
                    "asset_id": asset["id"],
                    "url": asset.get("url", ""),
                }
            )

    if failed:
        # Don't update the product if anything failed — half-set of images
        # is worse than no update. Surface the orphan uploads in `details`
        # AND give the caller explicit recovery guidance: under parallel
        # collect-then-decide, orphan count can reach N-1 (was 0..N-1 under
        # first-failure-break), so callers need to know what to do with them.
        error_message = (
            f"product_set_images: {len(failed)} of {len(paths)} upload(s) "
            f"failed. Product {product_id} NOT updated.\n"
            "Orphan asset_id(s) in details.uploaded — these exist in Voog's "
            "asset library but are NOT linked to any product. Recovery "
            "options:\n"
            "  1) Re-run product_set_images with the failed file(s) removed "
            "— successful orphans will be re-uploaded as new asset_ids.\n"
            "  2) Manually link the orphan asset_id(s) via Voog admin UI.\n"
            "  3) Delete orphan asset_id(s) via DELETE /assets/{id}."
        )
        return error_response(
            error_message,
            details={
                "product_id": product_id,
                "old_asset_ids": old_asset_ids,
                "uploaded": uploaded,
                "failed": failed,
            },
        )

    new_asset_ids = [u["asset_id"] for u in uploaded]

    # Voog gotcha 1: PUT envelope is `assets:[{id:n}]`, not `asset_ids`
    # (that's POST-only). Sending `asset_ids` on PUT silently keeps only
    # the first/hero image — gallery images vanish without an error.
    # See feedback_voog_assets_vs_asset_ids memory.
    #
    # Voog gotcha 2 (v1.5): one PUT does not reliably apply the ORDER.
    # Measured on the kolm-koma-2026 test site 2026-08-13 — 12 trials of a
    # random 7-asset order — 6 of 12 single PUTs came back with one or two
    # adjacent pairs transposed, returning 200 the whole time. A second
    # identical PUT produced the requested order in all 12. This tool's
    # contract is "first file becomes the main image, rest are gallery", so
    # a silently shuffled gallery breaks the one promise it makes.
    # Same failure and same remedy as media_set_set_assets; the loop lives
    # in voog._ordering so the two cannot drift.
    def _put_assets():
        return client.put(
            f"/products/{product_id}",
            {
                "image_id": new_asset_ids[0],
                "assets": [{"id": aid} for aid in new_asset_ids],
            },
            base=client.ecommerce_url,
        )

    def _read_order():
        product = client.get(f"/products/{product_id}", base=client.ecommerce_url)
        return list(product.get("asset_ids") or [])

    try:
        _, verified, final_order = put_ordered_with_readback(
            put=_put_assets,
            read_order=_read_order,
            wanted=new_asset_ids,
        )
    except Exception as e:
        return error_response(
            f"product_set_images: uploads OK but product {product_id} update "
            f"failed: {e}. Assets exist in Voog's library — re-link manually.",
            details={
                "product_id": product_id,
                "old_asset_ids": old_asset_ids,
                "uploaded": uploaded,
                "failed": failed,
            },
        )

    payload = {
        "product_id": product_id,
        "old_asset_ids": old_asset_ids,
        "new_asset_ids": new_asset_ids,
        "uploaded": uploaded,
        "failed": failed,
        "order_verified": verified,
    }
    if not verified:
        # Membership is right; the order is not. Reporting a clean ✓ here
        # would be the same lie 1.4.4 removed from media_set_set_assets.
        payload["stored_asset_ids"] = final_order
        return success_response(
            payload,
            summary=(
                f"⚠️ product {product_id}: {len(new_asset_ids)} image(s) attached, "
                f"but Voog did not apply the requested ORDER after 3 attempts. "
                f"Wanted {new_asset_ids}, Voog holds "
                f"{final_order or '(order could not be read back)'}. "
                "All images are linked — reorder in the Voog admin UI, or re-run."
            ),
        )

    summary = (
        f"🖼️ product {product_id}: {len(new_asset_ids)} image(s) set (main: id:{new_asset_ids[0]})"
    )
    return success_response(payload, summary=summary)


def _upload_asset(path: Path, client: VoogClient) -> dict:
    """Run the 3-step Voog asset upload protocol for a single file.

    Returns ``{id, url, width, height}`` on success. Raises on any step
    failure — caller catches and routes to the per-file ``failed`` list.
    """
    content_type = CONTENT_TYPES[path.suffix.lower()]
    size = path.stat().st_size

    # 1. Create asset record (admin/api default base, NOT ecommerce)
    asset = client.post(
        "/assets",
        {
            "filename": path.name,
            "content_type": content_type,
            "size": size,
        },
    )
    asset_id = asset["id"]
    upload_url = asset["upload_url"]
    _validate_upload_url(upload_url)

    # 2. Raw binary PUT to S3-style upload URL. Bypasses VoogClient because
    # this isn't JSON, isn't Voog-auth'd, and the URL is presigned.
    file_data = path.read_bytes()
    req = urllib.request.Request(
        upload_url,
        data=file_data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "x-amz-acl": "public-read",
        },
    )
    # 120s comfortably covers a multi-MB image over a slow link. Without a
    # bound, a hung S3 endpoint blocks the MCP request indefinitely (longer-
    # lived process than the voog.py CLI, where the same gap is less visible).
    # socket.timeout on expiry is caught by the outer try/except and routed
    # to the per-file `failed` list.
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"S3 upload failed: HTTP {resp.status}")

    # 3. Confirm — voog.py uses PUT (NOT POST as the handoff doc suggested)
    #
    # S10 retry-on-timeout: client._request's general policy is "don't
    # retry timeouts on writes" — a hung Voog endpoint should surface
    # fast rather than burn three timeout windows. The confirm step is
    # the one documented exception, because:
    #   1. It's idempotent under the same (asset_id) — Voog re-marks
    #      the same asset usable; no duplicate creation risk.
    #   2. Empirical observation in v1.3: confirm has a single-digit-%
    #      transient timeout rate (S3 backend is busy, asset is large),
    #      and a one-shot timeout has been the dominant cause of
    #      product_set_images failures requiring orphan asset cleanup.
    # Retry lives at the call site (NOT inside _request) so the general
    # policy is preserved — only this one call opts in.
    _CONFIRM_BACKOFFS = (0.5, 1.0, 2.0)  # before retry 1, 2, 3
    confirmed = None
    for attempt in range(4):  # 1 try + up to 3 retries = 4 attempts
        try:
            confirmed = client.put(f"/assets/{asset_id}/confirm")
            break
        except TimeoutError:
            if attempt >= 3:
                # Out of retries — propagate so the outer per-file ``failed``
                # list captures the timeout for caller recovery.
                raise
            time.sleep(_CONFIRM_BACKOFFS[attempt])
            # Loop continues to attempt+1.

    return {
        "id": asset_id,
        # Voog's OWN filename, which may differ from the local one: a
        # duplicate name is auto-suffixed (photo.jpg -> photo-1.jpg), so
        # echoing path.name back would name a file that does not exist.
        "filename": (confirmed.get("filename") if confirmed else None) or path.name,
        "url": confirmed.get("public_url", "") if confirmed else "",
        "width": confirmed.get("width") if confirmed else None,
        "height": confirmed.get("height") if confirmed else None,
    }
