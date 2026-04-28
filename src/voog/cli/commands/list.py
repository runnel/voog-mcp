"""voog list — show tracked files from manifest.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from voog.client import VoogClient


def add_arguments(subparsers):
    p = subparsers.add_parser("list", help="List tracked files (from manifest.json)")
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    manifest_path = Path.cwd() / "manifest.json"
    if not manifest_path.exists():
        sys.stderr.write("error: manifest.json missing. Run `voog pull` first.\n")
        return 1
    manifest = json.loads(manifest_path.read_text())
    for rel_path in sorted(manifest):
        entry = manifest[rel_path]
        print(f"  {rel_path:<60} id={entry['id']:<10} type={entry['type']}")
    return 0
