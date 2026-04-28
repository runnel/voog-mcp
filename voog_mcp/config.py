"""Config loading from MCP server env vars."""
import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required config env vars are missing."""


@dataclass
class Config:
    host: str
    api_token: str


def load_config() -> Config:
    host = os.environ.get("VOOG_HOST")
    token = os.environ.get("VOOG_API_TOKEN")
    if not host:
        raise ConfigError("VOOG_HOST env muutuja puudub")
    if not token:
        raise ConfigError("VOOG_API_TOKEN env muutuja puudub")
    return Config(host=host, api_token=token)
