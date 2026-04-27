"""Unit tests for VoogClient."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.client import VoogClient


class TestVoogClient(unittest.TestCase):
    def test_init_sets_base_urls(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.base_url, "https://runnel.ee/admin/api")
        self.assertEqual(client.ecommerce_url, "https://runnel.ee/admin/api/ecommerce/v1")

    def test_init_sets_headers(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.headers["X-API-Token"], "testtoken")
        self.assertEqual(client.headers["Content-Type"], "application/json")
