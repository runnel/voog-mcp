"""Tests for voog_mcp.__main__ entry point."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp import __main__ as main_module


class TestMainRuntimeError(unittest.TestCase):
    def test_runtime_error_exits_with_code_1(self):
        stderr = io.StringIO()
        with patch.object(main_module, "run_server") as mock_run, \
             patch.object(main_module.sys, "stderr", stderr):
            mock_run.side_effect = RuntimeError("VOOG_HOST env muutuja puudub")
            with self.assertRaises(SystemExit) as ctx:
                main_module.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("VOOG_HOST", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
