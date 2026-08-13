"""Write-then-verify helper for Voog's ordered-array endpoints.

Voog applies an ordered ``assets`` array only *partially* on the first
write, on at least two unrelated endpoints. The failure is not an error:
the request returns 200, membership is correct, and only the ORDER is
wrong — so a caller that trusts the status code reports a clean success
over a gallery that is visibly shuffled.

Measured on the kolm-koma-2026 test site, 2026-08-13:

  - ``PUT /media_sets/{id}`` — reversing 7 assets left two of them sharing
    position 6 and two pairs swapped, every attempt (v1.4.4).
  - ``PUT /products/{id}`` — 12 trials, random target order over 7 assets:
    **6 of 12 single PUTs came back in the wrong order**, always as one or
    two adjacent transpositions. A second identical PUT fixed it in every
    one of the 12 trials (max 2 PUTs needed).

Both endpoints converge on a repeat, so the fix is the same shape in both
places: PUT, read the order back, repeat while it disagrees, and tell the
caller when it never took rather than reporting success. This module holds
that loop once, because #75 established that logic duplicated across call
sites in this package drifts.

What differs per endpoint stays with the caller: ``/media_sets`` needs an
explicit 1-based ``position`` per entry (array order alone is not a signal
there), while ``/products`` derives order from array position and ignores
a ``position`` key entirely.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderedWriteResult:
    """Outcome of a write-read-retry cycle, in enough detail to describe it.

    A boolean "did it work" is not enough for the caller's message, because
    three different things can go wrong and they call for different advice:

      - the order never took after every attempt (``verified`` False,
        ``final`` non-empty) — Voog holds a known, wrong order;
      - the read-back itself failed (``verified`` False, ``read_failed``
        True) — nothing is known about the stored order, and in particular
        NOT that the write was correct;
      - a retry raised (``error`` set, ``writes_applied`` > 0) — an earlier
        write already landed, so "the target was not modified" is false.

    That last one is the reason this is a dataclass and not a 3-tuple: a
    caller that reports "update failed, nothing changed" after a successful
    first write and a failed second one sends the operator to a recovery
    procedure for a problem they do not have.
    """

    result: Any = None
    verified: bool = False
    final: list = field(default_factory=list)
    attempts: int = 0
    writes_applied: int = 0
    read_failed: bool = False
    error: Exception | None = None

    @property
    def target_modified(self) -> bool:
        """True when at least one write reached the server."""
        return self.writes_applied > 0


def put_ordered_with_readback(
    *,
    put: Callable[[], Any],
    read_order: Callable[[], list],
    wanted: list,
    attempts: int = 3,
) -> OrderedWriteResult:
    """Write an ordered array, read it back, and retry until the order took.

    ``put`` performs the write (its return value is passed through on
    :attr:`OrderedWriteResult.result`). ``read_order`` returns the ids
    currently stored, in stored order. ``wanted`` is the requested order.

    Never raises. A write that fails is recorded on ``error`` along with
    ``writes_applied``, so the caller can distinguish "nothing happened"
    from "the first write landed and the retry blew up" — the second is not
    a failure to report as "the target was not modified".

    A failing read-back is likewise recorded rather than raised: the write
    probably landed, and turning a verification hiccup into an error would
    be a worse lie than an honest "could not confirm". It is NOT recorded
    as success either — ``verified`` stays False and ``read_failed`` is set,
    because an unread order is an unknown order.
    """
    outcome = OrderedWriteResult()
    for _ in range(max(1, attempts)):
        outcome.attempts += 1
        try:
            outcome.result = put()
        except Exception as exc:
            outcome.error = exc
            return outcome
        outcome.writes_applied += 1
        try:
            outcome.final = list(read_order())
        except Exception:
            outcome.read_failed = True
            outcome.final = []
            return outcome
        if outcome.final == wanted:
            outcome.verified = True
            return outcome
    return outcome
