"""Shared test fixtures for CLI command tests.

Each ``cmd_*`` function in ``src/voog/cli/commands/`` is invoked as
``cmd(args, client)`` where ``args`` is an ``argparse.Namespace`` and
``client`` is a ``VoogClient``. Tests inject a ``MagicMock``-backed
client and a hand-built ``Namespace`` — no subprocess, no real HTTP,
no live API.

The ``cli_client`` fixture returns a pre-shaped ``MagicMock`` with the
attributes a real ``VoogClient`` exposes (``.host``, ``.base_url``,
``.ecommerce_url``) so commands that read those fields don't fail with
``AttributeError`` mid-call.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def cli_client():
    """A mock VoogClient with the surface CLI commands actually use.

    Methods (`get`, `get_all`, `post`, `put`, `delete`) are MagicMock
    attributes on the client and can be configured per-test:

        client.get_all.return_value = [...]
        client.post.return_value = {"id": 1, ...}
        client.put.side_effect = HTTPError(...)
    """
    client = MagicMock()
    client.host = "example.com"
    client.base_url = "https://example.com/admin/api"
    client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
    return client


def make_args(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from keyword arguments.

    Tests use this to stand in for the parsed CLI flags that argparse
    would produce, without going through the full subparser dispatch.
    """
    return argparse.Namespace(**kwargs)
