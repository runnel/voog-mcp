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

from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response
from voog_mcp.tools._helpers import validate_output_dir, write_json


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
                    "target_dir": {
                        "type": "string",
                        "description": "Absolute path where layouts/, components/, manifest.json are written",
                    },
                },
                "required": ["target_dir"],
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
                "to /layouts/{id}. Optional files=[\"layouts/x.tpl\", ...] "
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
                "required": ["target_dir"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(name: str, arguments: dict | None, client: VoogClient) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}

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
        return error_response(f"layouts_pull ebaõnnestus: {e}")

    layouts_written = 0
    components_written = 0
    per_layout_errors: list = []
    manifest: dict = {}

    layouts_dir = target / "layouts"
    components_dir = target / "components"

    for layout in layouts:
        lid = layout.get("id")
        title = layout.get("title")
        if not lid or not title:
            per_layout_errors.append({"layout_id": lid, "error": "missing id or title"})
            continue

        # /layouts list endpoint omits body — fetch detail per layout
        try:
            detail = client.get(f"/layouts/{lid}")
        except Exception as e:
            per_layout_errors.append({"layout_id": lid, "error": str(e)})
            continue

        body = detail.get("body", "") or ""
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

    results: list = []
    for rel_path in targets:
        info = manifest.get(rel_path)
        if info is None:
            results.append({
                "file": rel_path,
                "ok": False,
                "error": "not in manifest",
            })
            continue

        # voog.py-pulled trees mix type=layout and type=layout_asset entries.
        # Sending an asset id to PUT /layouts/{id} either 404s or — worst case
        # if the id-spaces collide — overwrites a real layout's body with a
        # CSS/JS payload. Bucket non-layout types as per-file failures.
        entry_type = info.get("type")
        if entry_type != "layout":
            results.append({
                "file": rel_path,
                "ok": False,
                "id": info.get("id"),
                "error": (
                    f"unsupported manifest type {entry_type!r}; layouts_push "
                    "only handles type='layout' (use voog.py CLI for asset push)"
                ),
            })
            continue

        full = target / rel_path
        if not full.exists():
            results.append({
                "file": rel_path,
                "ok": False,
                "error": "file missing on disk",
            })
            continue

        try:
            content = full.read_text(encoding="utf-8")
        except Exception as e:
            results.append({"file": rel_path, "ok": False, "error": f"read failed: {e}"})
            continue

        layout_id = info.get("id")
        try:
            client.put(f"/layouts/{layout_id}", {"body": content})
            results.append({"file": rel_path, "ok": True, "id": layout_id})
        except Exception as e:
            results.append({
                "file": rel_path,
                "ok": False,
                "id": layout_id,
                "error": str(e),
            })

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
