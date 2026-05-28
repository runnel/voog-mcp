"""Regression guard: transport-layer DEBUG loggers must not leak tokens.

httpcore + hpack emit raw HTTP/2 frames at DEBUG, including HPACK-encoded
``x-api-token`` headers. The Phase 6a redactor in :mod:`voog.client` only
covers voog-mcp's own log lines; the transport loggers sit below it.

:func:`voog.logging.silence_transport_loggers` raises those loggers to
WARNING so that an operator who enables Python root DEBUG does not
accidentally dump auth tokens. This test guards against a regression
where the helper is renamed, removed, or stops covering a transport
package we depend on.
"""

from __future__ import annotations

import logging

import voog.logging as voog_logging

# Names verified empirically against httpx[http2] (httpcore 1.x, hpack 4.x).
_TRANSPORT_LOGGER_NAMES = (
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpx",
    "hpack",
    "hpack.hpack",
    "hpack.table",
)


def _reset_logger_levels(names: tuple[str, ...]) -> None:
    """Restore loggers to NOTSET so tests don't bleed into each other."""
    for name in names:
        logging.getLogger(name).setLevel(logging.NOTSET)
    logging.getLogger().setLevel(logging.WARNING)


def test_silence_transport_loggers_raises_levels_to_warning() -> None:
    _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)
    try:
        logging.basicConfig(level=logging.DEBUG, force=True)

        voog_logging.silence_transport_loggers()

        for name in _TRANSPORT_LOGGER_NAMES:
            effective = logging.getLogger(name).getEffectiveLevel()
            assert effective >= logging.WARNING, (
                f"transport logger {name!r} effective level "
                f"{logging.getLevelName(effective)} would emit DEBUG/INFO "
                "frames (token leak risk)"
            )
    finally:
        _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)


def test_silence_transport_loggers_is_idempotent() -> None:
    _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)
    try:
        voog_logging.silence_transport_loggers()
        voog_logging.silence_transport_loggers()  # second call must not raise

        for name in _TRANSPORT_LOGGER_NAMES:
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    finally:
        _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)


def test_silence_transport_loggers_does_not_touch_voog_logger() -> None:
    """The 'voog' logger (used by voog-mcp's own redacted DEBUG output) must
    stay unaffected — operators legitimately want DEBUG there."""
    _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)
    voog_logger = logging.getLogger("voog")
    original = voog_logger.level
    voog_logger.setLevel(logging.DEBUG)
    try:
        voog_logging.silence_transport_loggers()
        assert voog_logger.level == logging.DEBUG, (
            "silence_transport_loggers must not affect the 'voog' logger; "
            "voog-mcp's own DEBUG output is PII-redacted and operators may "
            "want it on"
        )
    finally:
        voog_logger.setLevel(original)
        _reset_logger_levels(_TRANSPORT_LOGGER_NAMES)
