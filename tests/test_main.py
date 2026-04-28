"""Tests for voog.mcp.server main() entry point."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from voog.config import ConfigError
from voog.mcp import server as server_module


class TestMainConfigError(unittest.TestCase):
    def test_config_error_on_load_exits_with_code_1(self):
        """ConfigError raised by load_global_config() must produce sys.exit(1)."""
        stderr = io.StringIO()
        with patch.object(server_module, "load_global_config") as mock_load, \
             patch("sys.stderr", stderr), \
             patch("sys.argv", ["voog-mcp"]):
            mock_load.side_effect = ConfigError("config file not found")
            with self.assertRaises(SystemExit) as ctx:
                server_module.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("config file not found", stderr.getvalue())

    def test_other_runtime_error_propagates(self):
        """Unrelated RuntimeError must propagate as a real traceback, not be
        swallowed as a config error."""
        with patch.object(server_module, "load_global_config") as mock_load, \
             patch("sys.argv", ["voog-mcp"]):
            mock_load.side_effect = RuntimeError("unexpected failure")
            with self.assertRaises(RuntimeError) as ctx:
                server_module.main()
            self.assertEqual(str(ctx.exception), "unexpected failure")


if __name__ == "__main__":
    unittest.main()
