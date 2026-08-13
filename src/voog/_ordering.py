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
from typing import Any


def put_ordered_with_readback(
    *,
    put: Callable[[], Any],
    read_order: Callable[[], list],
    wanted: list,
    attempts: int = 3,
) -> tuple[Any, bool, list]:
    """Write an ordered array, read it back, and retry until the order took.

    ``put`` performs the write (its return value is passed through to the
    caller unchanged). ``read_order`` returns the ids currently stored, in
    stored order. ``wanted`` is the order the caller asked for.

    Returns ``(result, verified, final)``:

      - ``result`` — whatever the last ``put`` returned.
      - ``verified`` — True when a read-back matched ``wanted`` exactly.
        **False obliges the caller to say so** instead of reporting a clean
        success; the membership is right, the order is not.
      - ``final`` — the last order read back, or ``[]`` when the read
        itself failed.

    A failing read-back is treated as unverified rather than fatal: the
    write probably landed, and turning a verification hiccup into an error
    would be a worse lie than an honest "could not confirm".
    """
    result = None
    final: list = []
    for _ in range(max(1, attempts)):
        result = put()
        try:
            final = list(read_order())
        except Exception:
            # Verification is best-effort — a failed read must not undo,
            # or misreport, a write that probably succeeded.
            return result, False, []
        if final == wanted:
            return result, True, final
    return result, False, final
