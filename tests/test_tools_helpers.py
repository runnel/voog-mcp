"""Tests for voog.mcp.tools._helpers shared primitives."""

import json
import tempfile
import unittest
from pathlib import Path

from voog.mcp.tools._helpers import validate_output_dir, write_json


class TestValidateOutputDir(unittest.TestCase):
    def test_empty_returns_error(self):
        err = validate_output_dir("", tool_name="t", param_name="output_dir")
        self.assertIn("non-empty", err)
        self.assertIn("t:", err)
        self.assertIn("output_dir", err)

    def test_relative_path_returns_error(self):
        err = validate_output_dir("relative/dir", tool_name="t", param_name="target_dir")
        self.assertIn("absolute", err)
        self.assertIn("target_dir", err)
        self.assertIn("relative/dir", err)

    def test_absolute_path_returns_none(self):
        self.assertIsNone(validate_output_dir("/abs/path", tool_name="t", param_name="output_dir"))

    def test_param_name_appears_in_both_error_paths(self):
        empty_err = validate_output_dir("", tool_name="x", param_name="my_dir")
        rel_err = validate_output_dir("rel", tool_name="x", param_name="my_dir")
        self.assertIn("my_dir", empty_err)
        self.assertIn("my_dir", rel_err)


class TestWriteJson(unittest.TestCase):
    def test_writes_pretty_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            write_json(path, {"key": "väärtus", "list": [1, 2]})
            text = path.read_text(encoding="utf-8")
            # ensure_ascii=False keeps Estonian chars un-escaped
            self.assertIn("väärtus", text)
            # indent=2
            self.assertIn("\n  ", text)
            # Round-trip parses to identical structure
            self.assertEqual(json.loads(text), {"key": "väärtus", "list": [1, 2]})


if __name__ == "__main__":
    unittest.main()
