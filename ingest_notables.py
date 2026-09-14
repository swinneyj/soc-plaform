"""Ingest Splunk ES (or similar) fired notables into triage_results.

Usage (from project root):

    SOC_PLATFORM_ROOT=%CD% python ingest_notables.py --csv path/to/notables.csv

The CSV is expected to have columns that can be mapped to TriageResult fields.
By default, this script looks for:

    case_id, rule_id, rule_name, verdict, analysis_summary, remediation_steps, triaged_at

You can override column names via CLI flags.
"""

import argparse
import csv
import os
from datetime import datetime
from typing import Optional

from db.models import SessionLocal, TriageResult


def parse_datetime(value: Optional[str]) -> datetime:
    """Best-effort parsing of timestamp; fall back to now if parsing fails."""
    if not value:
        return datetime.utcnow()

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue

    # As a last resort, try datetime.fromisoformat
    try:
        return datetime.fromisoformat(value.strip())
    except Exception:
        return datetime.utcnow()


def ingest_notables(csv_path: str,
                    case_id_col: str,
                    rule_id_col: str,
                    rule_name_col: str,
                    verdict_col: str,
                    summary_col: str,
                    remediation_col: str,
                    time_col: str,
                    default_verdict: str = "suspicious",
                    default_confidence: float = 0.5) -> None:
    # Ensure SOC_PLATFORM_ROOT is set so db.models points at the right DB
    platform_root = os.environ.get("SOC_PLATFORM_ROOT")
    if not platform_root:
        os.environ["SOC_PLATFORM_ROOT"] = os.getcwd()

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    db = SessionLocal()
    try:
        created = 0
        updated = 0

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                case_id = (row.get(case_id_col) or "").strip()
                if not case_id:
                    # Skip rows without a case identifier
                    continue

                rule_id = (row.get(rule_id_col) or "").strip()
                rule_name = (row.get(rule_name_col) or "").strip()
                verdict = (row.get(verdict_col) or default_verdict).strip() or default_verdict

                analysis_summary = (row.get(summary_col) or "").strip()
                remediation_steps = (row.get(remediation_col) or "").strip()
                triaged_at_raw = row.get(time_col)
                triaged_at = parse_datetime(triaged_at_raw)

                # Idempotent upsert based on case_id
                existing = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
                if existing:
                    existing.rule_id = rule_id or existing.rule_id
                    existing.rule_name = rule_name or existing.rule_name
                    existing.verdict = verdict or existing.verdict
                    existing.analysis_summary = analysis_summary or existing.analysis_summary
                    existing.remediation_steps = remediation_steps or existing.remediation_steps
                    existing.triaged_at = triaged_at or existing.triaged_at
                    existing.confidence_score = existing.confidence_score or default_confidence
                    updated += 1
                else:
                    record = TriageResult(
                        case_id=case_id,
                        rule_id=rule_id or None,
                        rule_name=rule_name or "",
                        verdict=verdict,
                        confidence_score=default_confidence,
                        analysis_summary=analysis_summary,
                        remediation_steps=remediation_steps,
                        triaged_at=triaged_at,
                    )
                    db.add(record)
                    created += 1

        db.commit()
        print(f"Ingest complete. Created {created} triage cases, updated {updated}.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest fired notables into triage_results")
    parser.add_argument("--csv", required=True, help="Path to CSV export of fired notables")

    parser.add_argument("--case-id-column", default="case_id", help="Column name for case/notable ID")
    parser.add_argument("--rule-id-column", default="rule_id", help="Column name for rule_id")
    parser.add_argument("--rule-name-column", default="rule_name", help="Column name for rule_name")
    parser.add_argument("--verdict-column", default="verdict", help="Column name for verdict (benign/suspicious/malicious)")
    parser.add_argument("--summary-column", default="analysis_summary", help="Column name for analysis/description")
    parser.add_argument("--remediation-column", default="remediation_steps", help="Column name for remediation steps")
    parser.add_argument("--time-column", default="triaged_at", help="Column name for triage timestamp")

    parser.add_argument("--default-verdict", default="suspicious", help="Default verdict if column is missing/empty")
    parser.add_argument("--default-confidence", type=float, default=0.5, help="Default confidence score")

    args = parser.parse_args()

    ingest_notables(
        csv_path=args.csv,
        case_id_col=args.case_id_column,
        rule_id_col=args.rule_id_column,
        rule_name_col=args.rule_name_column,
        verdict_col=args.verdict_column,
        summary_col=args.summary_column,
        remediation_col=args.remediation_column,
        time_col=args.time_column,
        default_verdict=args.default_verdict,
        default_confidence=args.default_confidence,
    )


if __name__ == "__main__":
    main()
