"""Test voog.py layout-create command (POST /admin/api/layouts wrapper).

Hits live runnel.ee API. Creates a temp component, asserts new id in
manifest, then cleans up via DELETE.
"""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
import urllib.request

VOOG_PY = Path(__file__).resolve().parent.parent / "voog.py"


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


def _site_dir():
    """Where to run voog.py from. Must contain voog-site.json + manifest.json.

    Defaults to runnel-voog repo on dev box; override with VOOG_TEST_SITE_DIR
    env var (e.g. for CI or other developers).
    """
    env = os.environ.get("VOOG_TEST_SITE_DIR")
    if env:
        return Path(env)
    return Path("/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Isiklik/Runnel/runnel-voog")


class TestLayoutCreate(unittest.TestCase):
    def setUp(self):
        self.repo = _site_dir()
        if not (self.repo / "voog-site.json").exists():
            self.skipTest(
                f"voog-site.json puudub kohas {self.repo}; "
                "set VOOG_TEST_SITE_DIR to a runnel.ee Voog repo to run this test."
            )

    def test_layout_create_makes_new_layout_returns_id_updates_manifest(self):
        test_name = f"_test_2a_{int(time.time())}"
        test_path = self.repo / "components" / f"{test_name}.tpl"
        test_path.write_text(f"<!-- {test_name} -->\n<p>hello</p>\n")
        rel = f"components/{test_name}.tpl"
        new_id = None

        try:
            result = subprocess.run(
                ["python3", str(VOOG_PY), "layout-create", "component", rel],
                cwd=self.repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            self.assertIn("id:", result.stdout)

            manifest = json.loads((self.repo / "manifest.json").read_text())
            self.assertIn(rel, manifest, f"manifest missing {rel}")
            new_id = manifest[rel]["id"]
            self.assertIsInstance(new_id, int)
            self.assertGreater(new_id, 0)
            self.assertEqual(manifest[rel]["type"], "layout")
        finally:
            if new_id:
                try:
                    _delete_layout(new_id)
                except Exception as e:
                    print(f"⚠ Cleanup failed for layout {new_id}: {e}")
                manifest_path = self.repo / "manifest.json"
                if manifest_path.exists():
                    m = json.loads(manifest_path.read_text())
                    if rel in m:
                        del m[rel]
                        manifest_path.write_text(
                            json.dumps(m, indent=2, ensure_ascii=False) + "\n"
                        )
            if test_path.exists():
                test_path.unlink()

    def test_layout_create_makes_new_layout_with_content_type_page(self):
        """Non-component layouts need content_type=page in POST payload (Voog API requires it)."""
        test_name = f"_test_2a_layout_{int(time.time())}"
        test_path = self.repo / "layouts" / f"{test_name}.tpl"
        test_path.write_text(
            f"<!DOCTYPE html><html><body><!-- {test_name} -->{{% content %}}</body></html>\n"
        )
        rel = f"layouts/{test_name}.tpl"
        new_id = None

        try:
            result = subprocess.run(
                ["python3", str(VOOG_PY), "layout-create", rel],
                cwd=self.repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )

            manifest = json.loads((self.repo / "manifest.json").read_text())
            self.assertIn(rel, manifest)
            new_id = manifest[rel]["id"]

            # Verify content_type=page on the API side
            req = urllib.request.Request(
                f"https://runnel.ee/admin/api/layouts/{new_id}",
                headers={"X-API-Token": _api_key()},
            )
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            self.assertEqual(
                data.get("content_type"), "page",
                f"Layout missing content_type=page: {data}"
            )
        finally:
            if new_id:
                try:
                    _delete_layout(new_id)
                except Exception as e:
                    print(f"⚠ Cleanup failed for layout {new_id}: {e}")
                manifest_path = self.repo / "manifest.json"
                if manifest_path.exists():
                    m = json.loads(manifest_path.read_text())
                    if rel in m:
                        del m[rel]
                        manifest_path.write_text(
                            json.dumps(m, indent=2, ensure_ascii=False) + "\n"
                        )
            if test_path.exists():
                test_path.unlink()


if __name__ == "__main__":
    unittest.main()
