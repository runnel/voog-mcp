"""MCP tool for uploading files into Voog's media library (issue #140 item 3).

``asset_upload`` runs the same 3-step protocol as ``product_set_images`` but
leaves the result unattached, so the asset can be used anywhere: a
``site.data`` image map, an article gallery, a layout's markup. Before #137
this could not have worked at all — the SSRF allowlist rejected the very host
Voog hands out for step 2.

Three behaviours are the point of the tool, not incidental (all three cost a
downstream maintenance script several corrections to get right; see
:mod:`voog._assets`):

  1. **Look before uploading.** A repeated filename does not overwrite — Voog
     auto-suffixes it (``photo-1.jpg``) and the caller ends up with a
     duplicate while the original stays put. Default is to reuse the existing
     asset and report it as ``reused``.
  2. **Wait for derivatives via the API.** Resized copies appear
     asynchronously; asking the CDN for one too early earns a 403 that the
     CDN then caches for about an hour, poisoning a URL that is about to
     become valid.
  3. **Report the sizes Voog actually made.** Derivative widths follow the
     source aspect ratio, so a guessed width in a ``srcset`` 403s and the
     browser renders nothing rather than falling back.
"""

from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from voog._assets import (
    find_asset_by_filename,
    is_asset_complete,
    summarize_asset,
    wait_for_derivatives,
)
from voog._upload_validation import validate_upload_source
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.mcp.tools.products_images import CONTENT_TYPES, _upload_asset


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="asset_upload",
            description=(
                "Upload local image files into the site's media library "
                "(POST /assets -> PUT bytes -> PUT confirm) WITHOUT attaching "
                "them to anything. Use for images referenced from site.data / "
                "page.data maps, article galleries or template markup; use "
                "product_set_images when the images are a product's gallery.\n"
                "\n"
                "Returns each asset's id, dimensions, public path "
                "(/photos/<filename>) and the derivative `sizes` Voog actually "
                "produced — build srcsets from those widths, never from "
                "guessed ones (a width Voog did not make answers 403 and the "
                "browser renders nothing).\n"
                "\n"
                "By default an existing asset with the same filename is REUSED "
                "rather than uploaded again: Voog auto-suffixes duplicate "
                "filenames (photo-1.jpg), so re-uploading silently orphans the "
                "original. Pass allow_duplicate=true to force a new asset "
                "(e.g. a corrected re-shoot under a fresh sequence letter).\n"
                "\n"
                "Waits for Voog to finish its async resizes before returning "
                "— up to 120s per file, polling the API every 5s, so a large "
                "batch is slow by design. Each result carries "
                "`sizes_complete`: false means the wait timed out and `sizes` "
                "is PARTIAL, so build the srcset from a later read rather "
                "than from those widths. Pass wait_for_sizes=false to skip "
                "the wait entirely (then `sizes` is empty).\n"
                "\n"
                "Do NOT request a derivative URL over HTTP to check whether it "
                "exists — a too-early request gets a 403 that the CDN caches "
                "for ~1h, breaking a URL that was about to work."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Absolute paths to local image files "
                            f"({', '.join(sorted(CONTENT_TYPES))})"
                        ),
                    },
                    "allow_duplicate": {
                        "type": "boolean",
                        "description": (
                            "Upload even when an asset with this filename "
                            "already exists (default false = reuse it)"
                        ),
                        "default": False,
                    },
                    "wait_for_sizes": {
                        "type": "boolean",
                        "description": (
                            "Wait for Voog's async resizes before returning "
                            "(default true). false returns as soon as the "
                            "upload is confirmed — `sizes` may then be empty."
                        ),
                        "default": True,
                    },
                },
                "required": ["site", "files"],
            },
            annotations={
                "readOnlyHint": False,
                # Reads an arbitrary local path and PUBLISHES it at
                # /photos/<filename>. product_set_images carries the same
                # hint for the same reason: the host should be able to
                # prompt before a file leaves the machine.
                "destructiveHint": True,
                "idempotentHint": True,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset({"asset_upload"})


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")
    with client.with_tool(name):
        return _asset_upload(arguments, client)


def _asset_upload(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    files = arguments.get("files")
    if not isinstance(files, list) or not files:
        return error_response("asset_upload: files must be a non-empty array of paths")
    allow_duplicate = bool(arguments.get("allow_duplicate", False))
    # An explicit JSON null must not read as False — the schema default is
    # true, and silently skipping the poll would return an empty `sizes`.
    wait = arguments.get("wait_for_sizes")
    wait = True if wait is None else bool(wait)

    # Pre-flight every path before touching the API: a bad path found halfway
    # through would otherwise leave a partial upload set behind.
    paths: list[Path] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            return error_response("asset_upload: every entry in files must be a path string")
        if raw.startswith("~"):
            return error_response(
                f"asset_upload: {raw!r} must be an absolute path (~ is not expanded)"
            )
        path = Path(raw)
        if not path.is_absolute():
            return error_response(f"asset_upload: {raw!r} is not an absolute path")
        err = validate_upload_source(path, CONTENT_TYPES, tool_name="asset_upload")
        if err:
            return error_response(err)
        paths.append(path)

    # Voog names an asset by its basename, so two local files sharing one
    # would land as the SAME library entry — and with reuse on, the second
    # file's bytes would never be sent while the result still reported a
    # path. Caught here rather than resolved silently: only the caller knows
    # which one they meant.
    seen: dict[str, Path] = {}
    for path in paths:
        clash = seen.get(path.name)
        if clash is not None:
            return error_response(
                f"asset_upload: {str(clash)!r} and {str(path)!r} share the filename "
                f"{path.name!r}. Voog keys the library by filename, so one would "
                "overwrite or shadow the other. Rename one, or upload them in "
                "separate calls."
            )
        seen[path.name] = path

    uploaded: list[dict] = []
    reused: list[dict] = []
    failed: list[dict] = []

    # Sequential on purpose: each upload is a multi-MB binary PUT followed by
    # a poll loop, and the reuse check must see assets created earlier in the
    # same call (two paths with the same basename would otherwise race).
    for path in paths:
        try:
            if not allow_duplicate:
                existing = find_asset_by_filename(client, path.name)
                if existing:
                    # An asset found by lookup is already `done`, so its
                    # resizes are normally long finished — only poll when
                    # the record says otherwise (an upload still in flight
                    # from another session).
                    if wait and not is_asset_complete(existing):
                        existing = wait_for_derivatives(client, existing["id"])
                    reused.append(summarize_asset(existing))
                    continue
            result = _upload_asset(path, client)
            asset = (
                wait_for_derivatives(client, result["id"])
                if wait
                else {
                    "id": result["id"],
                    # Voog's stored name, not the local one — an auto-suffixed
                    # duplicate (photo.jpg -> photo-1.jpg) would otherwise be
                    # reported under a /photos/ path that 404s.
                    "filename": result.get("filename") or path.name,
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            )
            uploaded.append(summarize_asset(asset))
        except Exception as e:
            failed.append({"file": str(path), "error": str(e)})

    summary = f"🖼️  asset_upload: {len(uploaded)} uploaded, {len(reused)} reused"
    if failed:
        summary += f", {len(failed)} failed"
    if failed and not uploaded and not reused:
        # Nothing landed. isError is the signal callers branch on, so a
        # wholly-failed call must not read as success (product_set_images
        # takes the same line).
        return error_response(
            "asset_upload: every file failed — "
            + "; ".join(f"{f['file']}: {f['error']}" for f in failed)
        )
    return success_response(
        {
            "uploaded": uploaded,
            "reused": reused,
            "failed": failed,
        },
        summary=summary,
    )
