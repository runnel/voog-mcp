"""Tests for voog_mcp.__main__ entry point."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp import __main__ as main_module
from voog_mcp.config import ConfigError


class TestMainConfigError(unittest.TestCase):
    def test_config_error_exits_with_code_1(self):
        stderr = io.StringIO()
        with patch.object(main_module, "run_server") as mock_run, \
             patch.object(main_module.sys, "stderr", stderr):
            mock_run.side_effect = ConfigError("VOOG_HOST env muutuja puudub")
            with self.assertRaises(SystemExit) as ctx:
                main_module.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("VOOG_HOST", stderr.getvalue())

    def test_other_runtime_error_propagates(self):
        """Unrelated RuntimeError (e.g. asyncio "Event loop is closed") must not
        be re-skinned as a config error — it should propagate as a real traceback."""
        with patch.object(main_module, "run_server") as mock_run:
            mock_run.side_effect = RuntimeError("Event loop is closed")
            with self.assertRaises(RuntimeError) as ctx:
                main_module.main()
            self.assertEqual(str(ctx.exception), "Event loop is closed")


if __name__ == "__main__":
    unittest.main()
