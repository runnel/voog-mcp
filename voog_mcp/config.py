"""Config loading from MCP server env vars."""
import os
import sys
from dataclasses import dataclass


@dataclass
class Config:
    host: str
    api_token: str


def load_config() -> Config:
    host = os.environ.get("VOOG_HOST")
    token = os.environ.get("VOOG_API_TOKEN")
    if not host:
        sys.stderr.write("❌ VOOG_HOST env muutuja puudub\n")
        sys.exit(1)
    if not token:
        sys.stderr.write("❌ VOOG_API_TOKEN env muutuja puudub\n")
        sys.exit(1)
    return Config(host=host, api_token=token)
