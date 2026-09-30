#!/usr/bin/env python3
"""Normalize legacy judgment values in the database to the Phase 4 contract.

One-time migration (safe to re-run; idempotent). Dry-run by default — pass
--apply to write. Before applying, every old value is archived to a JSON
report under local-backups/ so the sweep is fully reversible alongside the
regular pg_dump backups.

What it normalizes:
  1. TriageResult verdict/confidence written by the deleted disposition
     mappers -> ("suspicious", 0.80), the only values today's promote path
     writes (an unverified alert at the closure-gate baseline).
  2. SupportiveQueryResult raw_result["question_resolution"] analyst labels
     -> "not_resolved", the only value today's save paths write.
  3. investigation_states.evidence_summary.resolved_questions keeps only
     resolutions backed by substantive targeted evidence; label-derived
     resolutions reopen (self-healing: the next analysis re-derives them).

Usage:
    .venv314/bin/python scripts/normalize_phase4_judgments.py            # preview
    .venv314/bin/python scripts/normalize_phase4_judgments.py --apply    # write
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import InvestigationState, SessionLocal, SupportiveQueryResult, TriageResult  # noqa: E402
from services import judgment_normalization as jn  # noqa: E402
from services import investigation_state as isvc  # noqa: E402


def build_plan(db):
    """Scan the database and return (plan, counts) without writing anything."""
    plan = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "triage_cases": [],
        "evidence_rows": [],
        "states": [],
        "evidence_rows_unparsed": 0,
    }

    for case in db.query(TriageResult).all():
        change = jn.plan_triage_normalization(case)
        if change:
            plan["triage_cases"].append(change)

    raws_by_case = {}
    for row in db.query(SupportiveQueryResult).all():
        raw = jn.parse_raw_result(row.raw_result)
        if raw is None:
            if row.raw_result:
                plan["evidence_rows_unparsed"] += 1
            continue
        raws_by_case.setdefault(row.case_id, []).append(raw)
        if (raw.get("question_resolution") or "not_resolved") != jn.PHASE4_RESOLUTION_LABEL:
            plan["evidence_rows"].append({
                "id": row.id,
                "case_id": row.case_id,
                "query_title": row.query_title,
                "old_label": raw.get("question_resolution"),
                "new_label": jn.PHASE4_RESOLUTION_LABEL,
            })

    for record in db.query(InvestigationState).all():
        summary = isvc._parse_json_object(record.evidence_summary)
        resolved = summary.get("resolved_questions") or []
        change = jn.plan_state_normalization(resolved, raws_by_case.get(record.case_id, []))
        if change:
            plan["states"].append({"case_id": record.case_id, **change})

    counts = {
        "triage_cases_to_normalize": len(plan["triage_cases"]),
        "evidence_labels_to_normalize": len(plan["evidence_rows"]),
        "states_to_normalize": len(plan["states"]),
        "resolutions_to_reopen": sum(len(s["removed_resolved"]) for s in plan["states"]),
        "evidence_rows_unparsed": plan["evidence_rows_unparsed"],
    }
    return plan, counts


def apply_plan(db, plan):
    """Apply the plan. Only rows listed in the plan are written."""
    cases_by_id = {case.case_id: case for case in db.query(TriageResult).all()}
    for change in plan["triage_cases"]:
        case = cases_by_id.get(change["case_id"])
        if case is not None:
            case.verdict = change["new_verdict"]
            case.confidence_score = change["new_confidence"]

    evidence_by_id = {
        row.id: row
        for row in db.query(SupportiveQueryResult)
        .filter(SupportiveQueryResult.id.in_([c["id"] for c in plan["evidence_rows"]] or [0]))
        .all()
    }
    for change in plan["evidence_rows"]:
        row = evidence_by_id.get(change["id"])
        if row is None:
            continue
        raw = jn.parse_raw_result(row.raw_result)
        if raw is not None:
            row.raw_result = json.dumps(jn.normalize_raw_result(raw), ensure_ascii=False)

    state_by_case = {
        record.case_id: record for record in db.query(InvestigationState).all()
    }
    for change in plan["states"]:
        record = state_by_case.get(change["case_id"])
        if record is None:
            continue
        summary = isvc._parse_json_object(record.evidence_summary)
        summary["resolved_questions"] = change["kept_resolved"]
        record.evidence_summary = json.dumps(summary, ensure_ascii=False)

    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the normalizations (default is a dry run)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan, counts = build_plan(db)
        if args.apply:
            apply_plan(db, plan)
    finally:
        db.close()

    archive_dir = ROOT / "local-backups"
    archive_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = archive_dir / f"phase4_judgment_migration_{stamp}.json"
    archive.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    mode = "APPLIED" if args.apply else "DRY RUN — nothing changed"
    print(f"[{mode}]")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"  archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
