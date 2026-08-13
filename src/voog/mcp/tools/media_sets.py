"""MCP tools for Voog media_sets (galleries).

Three tools — all use the Admin API (``client.base_url``):

  - ``media_set_get``                  — read-only, returns a curated view of
                                          a media_set (id, title, kind, and the
                                          ordered assets with their ids,
                                          positions, titles, filenames).
  - ``media_set_update_asset_titles``  — mutating but SAFE: edits one or more
                                          asset titles in place by GET-then-PUT,
                                          preserving every other asset's id,
                                          position, and existing title/settings.
  - ``media_set_set_assets``           — mutating and deliberately explicit:
                                          replaces the whole asset list, for
                                          building or reordering a gallery.
                                          Refuses to shorten the list without
                                          ``force`` (issue #140 item 5).

Why a typed tool exists at all
------------------------------
``PUT /media_sets/{id}`` with ``{"assets": [...]}`` is **replace-not-merge**:
any asset not in the request body is unlinked from the gallery — the exact
same destructive semantics as the product ``variants`` array (already guarded
in :mod:`voog.mcp.tools.products`). A naive "update one image's title" PUT —
``{"assets": [{"id": N, "title": "..."}]}`` — silently drops every other
image. This bit Stella live on 2026-05-20 (media_set 1542046: editing one alt
text unlinked 3 of 4 gallery images). See GitHub issue #120.

``media_set_update_asset_titles`` encapsulates the GET-then-PUT-full-array
pattern so the caller can change titles without ever holding the full array
themselves — mirroring how ``product_update`` shields callers from the
``variants`` foot-gun.

The generic ``voog_admin_api_call`` passthrough still reaches ``/media_sets``
for advanced edits (reordering, ``kind`` change, per-asset link settings); its
description carries an explicit destructive-PUT caveat.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import require_int, strip_site


def _simplify_media_set(media_set: dict) -> dict:
    """Curate the verbose GET shape down to the title-editing essentials."""
    assets = media_set.get("assets") or []
    simplified_assets = [
        {
            "id": a.get("id"),
            "position": a.get("position"),
            "title": a.get("title", ""),
            "filename": a.get("filename"),
            "type": a.get("type"),
        }
        for a in assets
        if isinstance(a, dict)
    ]
    return {
        "id": media_set.get("id"),
        "title": media_set.get("title", ""),
        "kind": media_set.get("kind"),
        "assets_count": len(simplified_assets),
        "assets": simplified_assets,
    }


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="media_set_get",
            description=(
                "Get a media_set (gallery) by id (`GET /media_sets/{id}`). "
                "Returns a curated view: media_set id, title, kind, and the "
                "ordered `assets` array with each asset's id, position, title, "
                "filename, and type. Read-only. Use this to discover asset ids "
                "and current titles before calling "
                "media_set_update_asset_titles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "media_set_id": {
                        "type": "integer",
                        "description": "Voog media_set (gallery) id",
                    },
                },
                "required": ["site", "media_set_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="media_set_update_asset_titles",
            description=(
                "Safely edit one or more asset titles in a media_set "
                "(gallery). Pass `titles` as an object mapping asset id -> new "
                'title, e.g. {"24898880": "New alt text"}.\n'
                "\n"
                "WHY THIS TOOL: `PUT /media_sets/{id}` is replace-not-merge — "
                "any asset omitted from the request body is unlinked from the "
                "gallery (same destructive semantics as product `variants`). "
                "Hand-rolling a partial-assets PUT to change one title silently "
                "drops every other image (hit live 2026-05-20). This tool does "
                "the GET-then-PUT-full-array dance for you: it reads the "
                "current media_set, applies only the requested title changes, "
                "and PUTs the complete asset array back — preserving every "
                "other asset's id, order, title, and link settings.\n"
                "\n"
                "Every key in `titles` must be an asset id already present in "
                "the media_set; an unknown id is rejected (no silent no-op). "
                "Titles may be empty strings (Voog allows clearing a title). "
                "Idempotent — re-running with the same titles is a no-op."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "media_set_id": {
                        "type": "integer",
                        "description": "Voog media_set (gallery) id",
                    },
                    "titles": {
                        "type": "object",
                        "description": (
                            "Map of asset id (string) -> new title (string). "
                            "Each id must already be in the media_set. Empty "
                            "string clears the title."
                        ),
                        "additionalProperties": {"type": "string"},
                        "minProperties": 1,
                    },
                },
                "required": ["site", "media_set_id", "titles"],
            },
            annotations={
                # Mutating, but the whole point is to be NON-destructive:
                # the full asset array is preserved. Reversible (re-run with
                # the previous titles) and idempotent.
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="media_set_set_assets",
            description=(
                "Set a media_set's FULL asset list in one call — for building "
                "a gallery from scratch or reordering/removing images "
                "(issue #140 item 5; media_set_update_asset_titles only edits "
                "titles of what is already there).\n"
                "\n"
                "`asset_ids` is the gallery's new content IN ORDER: position "
                "is array position. Any asset currently in the set but absent "
                "from the list is UNLINKED (the asset itself survives in the "
                "library; only its membership ends). Because that is easy to "
                "do by accident, a list SHORTER than the current one requires "
                "force=true.\n"
                "\n"
                "Titles and per-asset link settings are carried over for "
                "assets that stay; pass `titles` to set them for new ones. "
                "Upload files first with asset_upload to get ids."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "media_set_id": {
                        "type": "integer",
                        "description": "Voog media_set (gallery) id",
                    },
                    "asset_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "The gallery's complete new asset list, in display "
                            "order. Omitted assets are unlinked."
                        ),
                    },
                    "titles": {
                        "type": "object",
                        "description": (
                            "Optional map of asset id (string) -> title, for "
                            "assets being added. Existing titles are kept "
                            "unless overridden here."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Required when the new list is shorter than the "
                            "current one (i.e. the call removes images)."
                        ),
                        "default": False,
                    },
                },
                "required": ["site", "media_set_id", "asset_ids"],
            },
            annotations={
                "readOnlyHint": False,
                # Can unlink assets from the gallery — force-gated, but the
                # host should still be able to prompt.
                "destructiveHint": True,
                "idempotentHint": True,
            },
        ),
    ]


def _media_set_get(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    media_set_id = arguments.get("media_set_id")
    err = require_int("media_set_id", media_set_id, tool_name="media_set_get")
    if err:
        return error_response(err)
    try:
        media_set = client.get(f"/media_sets/{media_set_id}")
        simplified = _simplify_media_set(media_set)
        return success_response(
            simplified,
            summary=f"🖼️  media_set {media_set_id}: {simplified['assets_count']} assets",
        )
    except Exception as e:
        return error_response(f"media_set_get id={media_set_id} failed: {e}")


def _media_set_update_asset_titles(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    media_set_id = arguments.get("media_set_id")
    err = require_int("media_set_id", media_set_id, tool_name="media_set_update_asset_titles")
    if err:
        return error_response(err)

    titles = arguments.get("titles")
    if not isinstance(titles, dict) or not titles:
        return error_response(
            "media_set_update_asset_titles: `titles` must be a non-empty "
            "object mapping asset id -> new title"
        )
    # Normalise keys to strings (JSON object keys are strings; accept ints
    # defensively) and validate every value is a string.
    requested: dict[str, str] = {}
    for raw_key, value in titles.items():
        if not isinstance(value, str):
            return error_response(
                f"media_set_update_asset_titles: title for {raw_key!r} must be "
                f"a string (got {type(value).__name__})"
            )
        requested[str(raw_key)] = value

    # GET current state — needed to PUT the FULL array back (replace-not-merge).
    try:
        media_set = client.get(f"/media_sets/{media_set_id}")
    except Exception as e:
        return error_response(f"media_set_update_asset_titles GET id={media_set_id} failed: {e}")

    current_assets = media_set.get("assets") or []
    if not isinstance(current_assets, list):
        return error_response(
            f"media_set_update_asset_titles: media_set {media_set_id} returned "
            f"no usable `assets` array"
        )

    present_ids = {str(a.get("id")) for a in current_assets if isinstance(a, dict)}
    unknown = sorted(set(requested) - present_ids)
    if unknown:
        return error_response(
            f"media_set_update_asset_titles: asset id(s) {unknown} are not in "
            f"media_set {media_set_id}. Present ids: {sorted(present_ids)}. "
            "Run media_set_get first to confirm."
        )

    # Rebuild the FULL asset array in current position order, applying only
    # the requested title changes and preserving every other asset's id,
    # title, and link settings. Order is conveyed by array position (Voog's
    # PUT example carries no explicit `position` field), so sort by it.
    def _pos(a: dict):
        p = a.get("position")
        return p if isinstance(p, int) else 0

    ordered = sorted(
        (a for a in current_assets if isinstance(a, dict)),
        key=_pos,
    )
    new_assets = []
    changed = []
    for a in ordered:
        aid = a.get("id")
        key = str(aid)
        entry: dict = {"id": aid, "title": a.get("title", "")}
        # Preserve per-asset link settings (linkurl/linktarget) if present.
        settings = a.get("settings")
        if isinstance(settings, dict) and settings:
            entry["settings"] = settings
        if key in requested and requested[key] != entry["title"]:
            entry["title"] = requested[key]
            changed.append(key)
        new_assets.append(entry)

    # Defensive: never PUT fewer assets than we read — that would unlink
    # images, the exact bug this tool exists to prevent.
    if len(new_assets) != len(present_ids):
        return error_response(
            f"media_set_update_asset_titles: internal guard — rebuilt "
            f"{len(new_assets)} assets but media_set has {len(present_ids)}; "
            "refusing to PUT a shorter array."
        )

    if not changed:
        return success_response(
            _simplify_media_set(media_set),
            summary=(
                f"🖼️  media_set {media_set_id}: no title changes "
                f"(all {len(requested)} already current)"
            ),
        )

    # Bare body, no envelope — per Voog media_sets API docs.
    try:
        result = client.put(f"/media_sets/{media_set_id}", {"assets": new_assets})
    except Exception as e:
        return error_response(f"media_set_update_asset_titles PUT id={media_set_id} failed: {e}")

    simplified = _simplify_media_set(result) if isinstance(result, dict) else None
    return success_response(
        simplified if simplified else result,
        summary=(
            f"✓ media_set {media_set_id}: {len(changed)} title(s) updated, "
            f"{len(new_assets)} assets preserved"
        ),
    )


def _media_set_set_assets(
    arguments: dict, client: VoogClient
) -> list[TextContent] | CallToolResult:
    media_set_id = arguments.get("media_set_id")
    err = require_int("media_set_id", media_set_id, tool_name="media_set_set_assets")
    if err:
        return error_response(err)

    asset_ids = arguments.get("asset_ids")
    if not isinstance(asset_ids, list) or not asset_ids:
        return error_response(
            "media_set_set_assets: `asset_ids` must be a non-empty array of "
            "asset ids, in display order. To empty a gallery entirely, delete "
            "the media_set instead."
        )
    normalised: list[int] = []
    for raw in asset_ids:
        # bool is an int subclass — reject it explicitly, as require_int does.
        if isinstance(raw, bool) or not isinstance(raw, int):
            return error_response(f"media_set_set_assets: asset_ids must be integers (got {raw!r})")
        normalised.append(raw)
    if len(set(normalised)) != len(normalised):
        return error_response("media_set_set_assets: asset_ids contains duplicate ids")

    titles = arguments.get("titles") or {}
    if not isinstance(titles, dict):
        return error_response("media_set_set_assets: `titles` must be an object")
    requested_titles = {str(k): v for k, v in titles.items()}
    for key, value in requested_titles.items():
        if not isinstance(value, str):
            return error_response(
                f"media_set_set_assets: title for {key!r} must be a string "
                f"(got {type(value).__name__})"
            )

    try:
        media_set = client.get(f"/media_sets/{media_set_id}")
    except Exception as e:
        return error_response(f"media_set_set_assets GET id={media_set_id} failed: {e}")

    current_assets = [a for a in (media_set.get("assets") or []) if isinstance(a, dict)]
    current_by_id = {a.get("id"): a for a in current_assets}
    removed = [a.get("id") for a in current_assets if a.get("id") not in set(normalised)]

    # PUT /media_sets/{id} is replace-not-merge, so a shorter list silently
    # unlinks images — the exact failure that motivated issue #120. Make the
    # caller say it meant to.
    if removed and not arguments.get("force"):
        return error_response(
            f"media_set_set_assets: this would unlink {len(removed)} asset(s) "
            f"({sorted(str(r) for r in removed)}) from media_set {media_set_id}, "
            "because PUT replaces the whole array. Re-run with force=true if "
            "that is intended, or include those ids in asset_ids to keep them."
        )

    payload_assets = []
    for asset_id in normalised:
        existing = current_by_id.get(asset_id)
        entry: dict = {"id": asset_id}
        key = str(asset_id)
        if key in requested_titles:
            entry["title"] = requested_titles[key]
        elif existing is not None:
            entry["title"] = existing.get("title", "")
        settings = (existing or {}).get("settings")
        if isinstance(settings, dict) and settings:
            entry["settings"] = settings
        payload_assets.append(entry)

    try:
        result = client.put(f"/media_sets/{media_set_id}", {"assets": payload_assets})
    except Exception as e:
        return error_response(f"media_set_set_assets PUT id={media_set_id} failed: {e}")

    simplified = _simplify_media_set(result) if isinstance(result, dict) else None
    added = [a for a in normalised if a not in current_by_id]
    return success_response(
        simplified if simplified else result,
        summary=(
            f"🖼️  media_set {media_set_id}: {len(payload_assets)} assets set "
            f"({len(added)} added, {len(removed)} unlinked)"
        ),
    )


_DISPATCH = {
    "media_set_get": _media_set_get,
    "media_set_update_asset_titles": _media_set_update_asset_titles,
    "media_set_set_assets": _media_set_set_assets,
}


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})
    handler = _DISPATCH.get(name)
    if handler is None:
        return error_response(f"Unknown tool: {name}")
    # S9: tag every HTTP request inside this handler with X-MCP-Tool +
    # shared X-Request-Id. See VoogClient.with_tool docstring.
    with client.with_tool(name):
        return handler(arguments, client)
