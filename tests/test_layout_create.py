"""Test voog.py layout-create command (POST /admin/api/layouts wrapper).

Hits live runnel.ee API. Creates a temp component, asserts new id in
manifest, then cleans up via DELETE.
"""
import json
import subprocess
import time
from pathlib import Path
import urllib.request
import pytest

REPO = Path("/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Isiklik/Runnel/runnel-voog")
VOOG_PY = Path("/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/voog.py")


def _api_key():
    env_path = Path("/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/.env")
    for line in env_path.read_text().splitlines():
        if line.startswith("RUNNEL_VOOG_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("RUNNEL_VOOG_API_KEY not in .env")


def _delete_layout(layout_id):
    req = urllib.request.Request(
        f"https://runnel.ee/admin/api/layouts/{layout_id}",
        method="DELETE",
        headers={"X-API-Token": _api_key()},
    )
    urllib.request.urlopen(req)


def test_layout_create_makes_new_layout_returns_id_updates_manifest():
    test_name = f"_test_2a_{int(time.time())}"
    test_path = REPO / "components" / f"{test_name}.tpl"
    test_path.write_text(f"<!-- {test_name} -->\n<p>hello</p>\n")

    try:
        result = subprocess.run(
            ["python3", str(VOOG_PY), "layout-create", "component", str(test_path.relative_to(REPO))],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert "id:" in result.stdout

        manifest = json.loads((REPO / "manifest.json").read_text())
        rel = f"components/{test_name}.tpl"
        assert rel in manifest, f"manifest missing {rel}"
        new_id = manifest[rel]["id"]
        assert isinstance(new_id, int) and new_id > 0
        assert manifest[rel]["type"] == "layout"

        _delete_layout(new_id)
        del manifest[rel]
        (REPO / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    finally:
        if test_path.exists():
            test_path.unlink()
