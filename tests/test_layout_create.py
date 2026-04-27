"""Test voog.py layout-create command (POST /admin/api/layouts wrapper).

Two test classes:
- TestLayoutCreate: hits live runnel.ee API. Creates a temp layout/component,
  asserts new id in manifest, then cleans up via DELETE.
- TestLayoutCreateUnit: pure unit tests with mocked api_post + tempdir.
  Verifies payload shape (especially content_type override).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request

VOOG_PY = Path(__file__).resolve().parent.parent / "voog.py"

# Make voog importable for unit tests
sys.path.insert(0, str(VOOG_PY.parent))
import voog  # noqa: E402


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


class TestLayoutCreateUnit(unittest.TestCase):
    """Unit tests with mocked api_post — verifies payload shape, esp. content_type."""

    def _make_workspace(self, tmpdir, kind="layout", filename="Test.tpl", body="<!-- t -->"):
        tmpdir = Path(tmpdir)
        folder = "layouts" if kind == "layout" else "components"
        (tmpdir / folder).mkdir(parents=True)
        (tmpdir / folder / filename).write_text(body, encoding="utf-8")
        (tmpdir / "manifest.json").write_text("{}", encoding="utf-8")
        return tmpdir

    def test_layout_default_content_type_is_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = self._make_workspace(tmp, kind="layout", filename="Foo.tpl")
            with patch.object(voog, "LOCAL_DIR", tmp), \
                 patch.object(voog, "api_post") as mock_post:
                mock_post.return_value = {"id": 999, "updated_at": "2026-04-27"}
                voog.layout_create("layouts/Foo.tpl")
            args, _ = mock_post.call_args
            self.assertEqual(args[0], "/layouts")
            payload = args[1]
            self.assertEqual(payload["title"], "Foo")
            self.assertEqual(payload["component"], False)
            self.assertEqual(payload["content_type"], "page")

    def test_layout_explicit_content_type_blog_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = self._make_workspace(tmp, kind="layout", filename="Single article.tpl")
            with patch.object(voog, "LOCAL_DIR", tmp), \
                 patch.object(voog, "api_post") as mock_post:
                mock_post.return_value = {"id": 1000, "updated_at": "2026-04-27"}
                voog.layout_create("layouts/Single article.tpl", content_type="blog_article")
            args, _ = mock_post.call_args
            payload = args[1]
            self.assertEqual(payload["content_type"], "blog_article")

    def test_component_ignores_content_type(self):
        """Components don't use content_type — Voog API ignores it; we omit it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = self._make_workspace(tmp, kind="component", filename="header.tpl")
            with patch.object(voog, "LOCAL_DIR", tmp), \
                 patch.object(voog, "api_post") as mock_post:
                mock_post.return_value = {"id": 1001, "updated_at": "2026-04-27"}
                voog.layout_create("components/header.tpl", content_type="blog_article")
            args, _ = mock_post.call_args
            payload = args[1]
            self.assertEqual(payload["component"], True)
            self.assertNotIn("content_type", payload)


if __name__ == "__main__":
    unittest.main()
