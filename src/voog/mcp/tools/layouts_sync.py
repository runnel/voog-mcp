"""MCP tools for syncing Voog layouts between API and local filesystem.

Two filesystem-touching tools:

  - ``layouts_pull(target_dir)``  — fetch every layout (and component) via
                                      ``/layouts`` + ``/layouts/{id}``, write
                                      per-layout ``.tpl`` files into ``layouts/``
                                      and ``components/`` subdirs, build
                                      ``manifest.json`` mapping local paths → ids.
                                      Refuses to overwrite an existing tree
                                      that already contains ``.tpl`` files.
  - ``layouts_push(target_dir, files)`` — read ``manifest.json`` + ``.tpl``
                                      files from ``target_dir`` and PUT each to
                                      ``/layouts/{id}``. Optional ``files``
                                      filter pushes only the named relative
                                      paths. Returns per-file success/failure
                                      breakdown — partial failure does not
                                      abort the rest of the push.

Manifest format (matches ``voog.py`` CLI shape so MCP-pulled and CLI-pulled
trees are interchangeable):
    {
      "<rel_path>": {"id": <int>, "type": "layout", "updated_at": "<iso>"},
      ...
    }

Annotations: ``readOnlyHint=False`` (writes disk and/or API),
``destructiveHint=False`` (additive — pull writes to a fresh dir; push
updates existing layouts but Voog retains version history),
``idempotentHint=True`` (same input → same end state).
"""

import json
from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from voog._concurrency import parallel_map
from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site, validate_output_dir, write_json


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="layouts_pull",
            description=(
                "Fetch every layout + component from /layouts and write per-layout "
                ".tpl files to target_dir/layouts/ and target_dir/components/. "
                "Builds manifest.json mapping each local path to {id, type, "
                "updated_at}. REFUSES to overwrite an existing tree that already "
                "contains .tpl files — pick a fresh location or clear it first. "
                "Empty/non-tpl content in target_dir is fine (e.g. README.md, "
                ".gitignore are preserved)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "target_dir": {
                        "type": "string",
                        "description": "Absolute path where layouts/, components/, manifest.json are written",
                    },
                },
                "required": ["site", "target_dir"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layouts_push",
            description=(
                "Read manifest.json + .tpl files from target_dir and PUT each "
                'to /layouts/{id}. Optional files=["layouts/x.tpl", ...] '
                "filter pushes only the named relative paths. files=null (or "
                "omitted) pushes every type=layout entry in the manifest "
                "(non-layout entries — e.g. type=layout_asset from voog.py-"
                "pulled trees — are captured as per-file failures rather "
                "than mis-PUT to /layouts/{id}). Returns per-file success/"
                "failure breakdown; missing files and PUT errors are captured "
                "per-entry and do not abort the remaining pushes. Recommended "
                "pre-flight: site_snapshot for full backup before a mass push."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name from voog_list_sites"},
                    "target_dir": {
                        "type": "string",
                        "description": "Absolute path of a previously-pulled tree (must contain manifest.json)",
                    },
                    "files": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Optional list of relative paths to push (e.g. 'layouts/default.tpl'). Null/omitted = push all manifest entries.",
                    },
                },
                "required": ["site", "target_dir"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "layouts_pull":
        return _layouts_pull(arguments, client)

    if name == "layouts_push":
        return _layouts_push(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _layouts_pull(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    target_dir = arguments.get("target_dir") or ""
    err = validate_output_dir(target_dir, tool_name="layouts_pull", param_name="target_dir")
    if err:
        return error_response(err)

    target = Path(target_dir)
    # Refuse if target_dir already has .tpl files anywhere — caller must
    # explicitly clear stale templates rather than risk a half-merged tree
    # where deleted-on-Voog layouts linger locally.
    if target.exists():
        existing_tpl = list(target.rglob("*.tpl"))
        if existing_tpl:
            return error_response(
                f"layouts_pull: target_dir {target_dir!r} already contains "
                f"{len(existing_tpl)} .tpl file(s). Pick a fresh location or "
                "clear existing templates first."
            )

    try:
        target.mkdir(parents=True, exist_ok=True)
        layouts = client.get_all("/layouts")
    except Exception as e:
        return error_response(f"layouts_pull failed: {e}")

    layouts_written = 0
    components_written = 0
    per_layout_errors: list = []
    manifest: dict = {}

    layouts_dir = target / "layouts"
    components_dir = target / "components"

    # Phase A — validate layouts list, then fetch every valid /layouts/{id}
    # detail body in parallel. /layouts list endpoint omits body, so each
    # detail is its own GET. max_workers=8 per spec § 4.3 (read-only fetches).
    valid_layouts: list = []
    for layout in layouts:
        lid = layout.get("id")
        title = layout.get("title")
        if not lid or not title:
            per_layout_errors.append({"layout_id": lid, "error": "missing id or title"})
            continue
        valid_layouts.append(layout)

    detail_urls = [f"/layouts/{layout['id']}" for layout in valid_layouts]
    fetch_results = parallel_map(client.get, detail_urls, max_workers=8)

    # Phase B — sequential write loop. Sync filesystem I/O is fast; serial
    # writes keep manifest assembly atomic and avoid mkdir/write races with
    # no measurable speedup if parallelized.
    for layout, (_url, detail, exc) in zip(valid_layouts, fetch_results, strict=True):
        lid = layout["id"]
        title = layout["title"]
        if exc is not None:
            per_layout_errors.append({"layout_id": lid, "error": str(exc)})
            continue

        # Voog admins can put anything in a layout title — guard against path
        # separators and parent-dir tokens so a hostile or buggy title cannot
        # write outside target_dir/{layouts,components}/.
        if "/" in title or "\\" in title or ".." in title:
            per_layout_errors.append(
                {
                    "layout_id": lid,
                    "error": f"title contains path-unsafe characters: {title!r}",
                }
            )
            continue

        body = (detail or {}).get("body", "") or ""
        is_component = bool(layout.get("component"))
        folder = components_dir if is_component else layouts_dir
        folder.mkdir(exist_ok=True)
        rel_path = f"{'components' if is_component else 'layouts'}/{title}.tpl"
        (target / rel_path).write_text(body, encoding="utf-8")

        manifest[rel_path] = {
            "id": lid,
            "type": "layout",
            "updated_at": layout.get("updated_at", ""),
        }
        if is_component:
            components_written += 1
        else:
            layouts_written += 1

    manifest_path = target / "manifest.json"
    write_json(manifest_path, manifest)

    summary = (
        f"📥 layouts_pull: {layouts_written} layouts + {components_written} components "
        f"→ {target_dir}"
    )
    if per_layout_errors:
        summary += f" ({len(per_layout_errors)} per-layout errors)"

    return success_response(
        {
            "target_dir": target_dir,
            "layouts_written": layouts_written,
            "components_written": components_written,
            "manifest_path": str(manifest_path),
            "per_layout_errors": per_layout_errors,
        },
        summary=summary,
    )


def _layouts_push(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    target_dir = arguments.get("target_dir") or ""
    err = validate_output_dir(target_dir, tool_name="layouts_push", param_name="target_dir")
    if err:
        return error_response(err)

    target = Path(target_dir)
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        return error_response(
            f"layouts_push: manifest.json missing in {target_dir!r}. "
            "Run layouts_pull first to materialize a tree."
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return error_response(f"layouts_push: manifest.json unreadable: {e}")

    files_arg = arguments.get("files")
    if files_arg is None:
        targets = list(manifest.keys())
    else:
        targets = list(files_arg)

    # Two-pass: per-file pre-PUT validation runs sequentially (dict lookups +
    # tiny disk reads — fast, cheap, deterministic), THEN PUTs fan out via
    # parallel_map. Pre-PUT failures (not-in-manifest, wrong type, file missing,
    # read error) get appended to a results-by-rel-path map; PUT-eligible
    # entries are collected as (rel_path, layout_id, content) tuples and PUT'd
    # in parallel. Final ``results`` list is rebuilt in input order to preserve
    # the existing per-file shape exactly.
    results_by_path: dict = {}
    put_items: list = []  # list of (rel_path, layout_id, content)

    for rel_path in targets:
        info = manifest.get(rel_path)
        if info is None:
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "error": "not in manifest",
            }
            continue

        # voog.py-pulled trees mix type=layout and type=layout_asset entries.
        # Sending an asset id to PUT /layouts/{id} either 404s or — worst case
        # if the id-spaces collide — overwrites a real layout's body with a
        # CSS/JS payload. Bucket non-layout types as per-file failures.
        entry_type = info.get("type")
        if entry_type != "layout":
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "id": info.get("id"),
                "error": (
                    f"unsupported manifest type {entry_type!r}; layouts_push "
                    "only handles type='layout' (use voog.py CLI for asset push)"
                ),
            }
            continue

        full = target / rel_path
        # Defense-in-depth: even with the pull-side title sanitizer in place, a
        # hand-edited or corrupted manifest could still smuggle a "../" entry.
        # Refuse to read or PUT anything that resolves outside target_dir.
        resolved = full.resolve()
        target_resolved = target.resolve()
        if not resolved.is_relative_to(target_resolved):
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "error": "rel_path escapes target directory (path traversal blocked)",
            }
            continue

        if not full.exists():
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "error": "file missing on disk",
            }
            continue

        try:
            content = full.read_text(encoding="utf-8")
        except Exception as e:
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "error": f"read failed: {e}",
            }
            continue

        put_items.append((rel_path, info.get("id"), content))

    # Writes are more sensitive than reads — max_workers=4 (spec § 4.3).
    def _put_one(item):
        rel_path, layout_id, content = item
        return client.put(f"/layouts/{layout_id}", {"body": content})

    parallel_results = parallel_map(_put_one, put_items, max_workers=4)
    for (rel_path, layout_id, _content), _result, exc in parallel_results:
        if exc is None:
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": True,
                "id": layout_id,
            }
        else:
            results_by_path[rel_path] = {
                "file": rel_path,
                "ok": False,
                "id": layout_id,
                "error": str(exc),
            }

    # Rebuild in original input order so per-file shape is byte-for-byte
    # identical to the pre-parallelization output.
    results: list = [results_by_path[rel_path] for rel_path in targets]

    succeeded = sum(1 for r in results if r["ok"])
    failed = len(results) - succeeded
    summary = f"📤 layouts_push: {succeeded}/{len(results)} files pushed"
    if failed:
        summary += f" ({failed} failed)"

    return success_response(
        {
            "target_dir": target_dir,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        },
        summary=summary,
    )
