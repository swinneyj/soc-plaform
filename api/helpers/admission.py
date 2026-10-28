"""Shared admission gates for the API's request entry points.

Every route that must refuse work BEFORE doing expensive work
(job-queue saturation, payload byte budgets, mode latches,
per-resource concurrency caps)
should decide here, so the boundary semantics (inclusive vs
exclusive limits), status codes, and detail shapes stay
consistent across admission sites.

Pure decision helpers: the caller measures the inputs (job
counts, encoded byte sizes, the configured mode) and raises
the returned exception. Returning the exception instead of
raising it here keeps this module free of route context, so
the gates stay unit-testable without an HTTP round trip.
"""

from typing import Optional, Sequence

from fastapi import HTTPException


def queue_rejection(
    current: int, limit: int, label: str = "Job queue"
) -> Optional[HTTPException]:
    """Admission gate for a bounded work queue (429 when full).

    Admits while ``current < limit``; a queue AT its limit is
    full and rejects — the inclusive boundary matters: the next
    job would exceed the cap, so it must not be admitted.
    ``current`` must count unfinished work across every store
    that holds it (e.g. in-memory jobs AND persisted rows), or
    the cap silently resets when a store empties (restart).
    """
    if current < limit:
        return None
    return HTTPException(
        status_code=429,
        detail=f"{label} is full ({current} unfinished >= {limit}); retry later",
    )


def payload_rejection(
    size_bytes: int, max_bytes: int, label: str = "Paste payload"
) -> Optional[HTTPException]:
    """Admission gate for a byte budget (413 when over budget).

    ``size_bytes`` must be the payload's real encoded size —
    e.g. ``len(text.encode("utf-8"))`` — never a character
    count; multibyte text can otherwise carry several times
    the budget past the gate. At the budget exactly the payload
    is admitted (exclusive boundary): the budget is the
    maximum, not one-less-than-the-maximum.
    """
    if size_bytes <= max_bytes:
        return None
    return HTTPException(
        status_code=413,
        detail=f"{label} exceeds {max_bytes} bytes",
    )


def latch_rejection(
    mode: str,
    permitted_modes: Sequence[str],
    refusal_detail: str,
) -> Optional[HTTPException]:
    """Fail-closed admission latch (403 outside permitted modes).

    A mode admits only when it is explicitly listed; anything
    else — including an unrecognized mode — refuses, so a
    typo'd config can never widen the door. The refusal text is
    the caller's, so the operator sees the exact config change
    that would open the latch.
    """
    if mode in permitted_modes:
        return None
    return HTTPException(status_code=403, detail=refusal_detail)


def concurrency_rejection(
    in_flight: bool,
    label: str = "task",
    resource: str = "resource",
    cap: int = 1,
) -> Optional[HTTPException]:
    """Admission gate for a per-resource concurrency cap (409 while occupied).

    Where ``queue_rejection`` measures GLOBAL saturation and
    answers 429 (retry later), this gate measures occupancy of
    one named resource — a case, a job id — and answers 409:
    the request conflicts with work already running against the
    SAME resource, so a different resource may proceed but a
    re-issue of this one must not. ``in_flight`` is the caller's
    occupancy probe (e.g. membership of the case id in the
    inflight set), taken under the caller's lock.
    """
    if not in_flight:
        return None
    return HTTPException(
        status_code=409,
        detail=(
            f"A {label} is already running for this {resource} "
            f"(per-{resource} concurrency cap is {cap})"
        ),
    )
