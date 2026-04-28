"""Local proxy server: serves live site HTML, swaps known assets with
local copies. Used during template development — edit a local CSS/JS file,
refresh the browser, see the change immediately.

Asset discovery: scans ``<repo>/javascripts/*.js`` and ``<repo>/stylesheets/*.css``
at startup. Filename collisions across folders are not handled (last-write-wins
in dict order — kept simple).
"""
from __future__ import annotations

from pathlib import Path

ASSET_DIRS = {
    "javascripts": (".js",),
    "stylesheets": (".css",),
}


def discover_local_assets(root: Path) -> dict[str, str]:
    """Return ``{filename: relative_path_from_root}`` for every JS/CSS file
    under ``root/javascripts/`` and ``root/stylesheets/``."""
    out: dict[str, str] = {}
    for folder, extensions in ASSET_DIRS.items():
        folder_path = root / folder
        if not folder_path.is_dir():
            continue
        for child in folder_path.iterdir():
            if child.is_file() and child.suffix in extensions:
                out[child.name] = f"{folder}/{child.name}"
    return out


# The HTTP-proxy implementation (`serve(client, local_dir, port)`) is a
# straightforward port of voog.py:1500-1700 with these substitutions:
#   - LOCAL_ASSETS dict → discover_local_assets(local_dir) at startup
#   - Estonian print() / docstring → English
#   - Removed reference to "stellasoomlais-voog/"
#
# The full serve() is ~150 lines; copy it during the CLI extraction (Task 11).
