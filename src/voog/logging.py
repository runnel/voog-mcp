"""Defensive logging configuration for voog-mcp.

Phase 6a S-1 added PII redaction (``_redact_headers`` /
``_QUERY_STRING_VALUE_CAP``) to :mod:`voog.client`'s DEBUG output, but
that only covers voog-mcp's own log lines. The transport stack
(``httpx`` → ``httpcore`` → ``hpack``) sits below voog-mcp and emits
raw HTTP/2 frames at DEBUG, including HPACK-encoded ``x-api-token``
headers. A v1.4 smoke test on 2026-05-28 confirmed that calling
``logging.basicConfig(level=DEBUG)`` from a user script was enough to
leak a token into the conversation transcript.

:func:`silence_transport_loggers` is the central point of suppression:
voog-mcp's CLI and MCP server call it once at startup, which clamps the
known-leaky loggers to WARNING. Operators who genuinely need raw
transport DEBUG must re-enable them explicitly *after* startup.
"""

from __future__ import annotations

import logging

# Empirically verified against httpx[http2] (httpcore 1.x, hpack 4.x, h2 4.x).
# Each entry is a logger that has been observed — or is a parent of a logger
# that has been observed — emitting raw header bytes or HPACK frames at DEBUG
# level.
#
# Why both parents AND children are listed (instead of relying on Python's
# logger hierarchy to propagate WARNING from the parent down): a parent's
# ``setLevel(WARNING)`` only takes effect for children whose level is
# ``NOTSET``. If a parent process, a pytest fixture, or a debug shim has
# already called ``logging.getLogger("httpcore.http2").setLevel(DEBUG)``
# *before* voog-mcp's entry point runs, a parent-only clamp would leave that
# child at DEBUG. The redundant explicit list actively resets the known
# leaky children — belt and suspenders.
#
# Verified non-leaky (as of 2026-05-28, h2 4.3.0): the ``h2`` package
# registers no loggers under its own name — nothing to clamp.
#
# Maintenance trigger: if ``httpx[http2]`` upgrades to a major version, re-run
# the smoke (``logging.basicConfig(level=DEBUG)`` + a real authenticated
# request) and append any new logger names that emit header bytes here.
_TRANSPORT_LOGGER_NAMES: tuple[str, ...] = (
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpx",
    "hpack",
    "hpack.hpack",
    "hpack.table",
)


def silence_transport_loggers() -> None:
    """Clamp known-leaky transport loggers to WARNING.

    Idempotent. Safe to call from every entry point. Does not touch the
    ``voog`` logger — voog-mcp's own DEBUG output is PII-redacted and
    operators may legitimately want it on.
    """
    for name in _TRANSPORT_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)
