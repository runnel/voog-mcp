"""Tests for voog_mcp.config."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.config import load_config


class TestLoadConfig(unittest.TestCase):
    def test_missing_host_exits(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VOOG_HOST", None)
            os.environ.pop("VOOG_API_TOKEN", None)
            with self.assertRaises(SystemExit):
                load_config()

    def test_missing_token_exits(self):
        with patch.dict(os.environ, {"VOOG_HOST": "runnel.ee"}, clear=True):
            with self.assertRaises(SystemExit):
                load_config()

    def test_both_set_returns_config(self):
        env = {"VOOG_HOST": "runnel.ee", "VOOG_API_TOKEN": "abc"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()
            self.assertEqual(cfg.host, "runnel.ee")
            self.assertEqual(cfg.api_token, "abc")
