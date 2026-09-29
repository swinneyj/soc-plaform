"""Retention pruning for ``analysis_results`` (Phase 5 ops readiness).

The analysis table grows unbounded: every loop iteration persists a prompt +
response pair, and long-lived cases accumulate dozens. Policy (plan §7):

- keep the last ``keep_last_n`` analysis rows per case (default 20), and
- keep *everything* for closure-linked cases — any case with a closure note
  (draft, submitted, or closed) is part of the permanent record, and the
  note's iteration table references the analyses it was built from.

Selection is a pure function so the policy is unit-testable; the DB wrapper
just loads rows, delegates, and deletes. Pruning is dry-run by default and
is meant to run from ``scripts/prune_analysis_results.py`` (cron/ops), never
on the request path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

DEFAULT_KEEP_LAST_N = 20


def select_prunable_analysis_ids(
    rows: Sequence[Any],
    keep_last_n: int = DEFAULT_KEEP_LAST_N,
    protected_case_ids: Optional[Iterable[str]] = None,
) -> List[int]:
    """Return ids of analysis rows eligible for deletion.

    ``rows`` are duck-typed ``AnalysisResult`` objects (needs ``id``,
    ``case_id``, ``created_at``). ``protected_case_ids`` are never touched.
    Within each unprotected case, the newest ``keep_last_n`` rows (by
    ``created_at``, id as tiebreak) survive; everything older is prunable.
    """
    protected: Set[str] = {str(c) for c in (protected_case_ids or []) if str(c)}
    keep = max(0, int(keep_last_n))

    by_case: Dict[str, List[Any]] = {}
    for row in rows:
        case_id = str(getattr(row, "case_id", "") or "")
        by_case.setdefault(case_id, []).append(row)

    prunable: List[int] = []
    for case_id, case_rows in by_case.items():
        if case_id in protected:
            continue
        # Newest first; id breaks created_at ties deterministically.
        ordered = sorted(
            case_rows,
            key=lambda r: (getattr(r, "created_at", None) or datetime.min, getattr(r, "id", 0) or 0),
            reverse=True,
        )
        prunable.extend(int(r.id) for r in ordered[keep:])
    return sorted(prunable)


def prune_analysis_results(
    db: Any,
    keep_last_n: int = DEFAULT_KEEP_LAST_N,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Apply the retention policy to the database.

    Returns a summary: {scanned, kept, prunable, deleted, protected_cases,
    keep_last_n, dry_run}. Deletes only when ``dry_run`` is False.
    """
    from db.models import AnalysisResult, ClosureNote  # local import: services stay import-pure

    rows = db.query(AnalysisResult).all()
    protected = {
        str(row.case_id)
        for row in db.query(ClosureNote.case_id).distinct().all()
        if row.case_id
    }
    prunable_ids = select_prunable_analysis_ids(rows, keep_last_n, protected)

    deleted = 0
    if prunable_ids and not dry_run:
        deleted = (
            db.query(AnalysisResult)
            .filter(AnalysisResult.id.in_(prunable_ids))
            .delete(synchronize_session=False)
        )
        db.commit()

    return {
        "scanned": len(rows),
        "kept": len(rows) - len(prunable_ids),
        "prunable": len(prunable_ids),
        "deleted": deleted,
        "protected_cases": len(protected),
        "keep_last_n": keep_last_n,
        "dry_run": dry_run,
    }
