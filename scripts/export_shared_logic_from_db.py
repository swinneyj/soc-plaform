"""Export shared rule logic from PostgreSQL into reviewable JSON files.

This script reads `ESCorrelationRule` and `SupportiveQuery` rows from the
active database backend and writes JSON snapshots that match the repo-managed
shapes used by `sample_rules.json` and `supportive_rules.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.models import ESCorrelationRule, SessionLocal, SupportiveQuery  # noqa: E402


def default_export_dir() -> Path:
    export_dir = REPO_ROOT / "local-backups" / "shared-logic-export"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def parse_json_array(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        parsed_value = json.loads(raw_value)
        if isinstance(parsed_value, list):
            return parsed_value
    except json.JSONDecodeError:
        pass
    return []


def build_rules_payload() -> dict:
    database_session = SessionLocal()
    try:
        rule_rows = (
            database_session.query(ESCorrelationRule)
            .order_by(ESCorrelationRule.rule_id.asc())
            .all()
        )
        rules_payload = []
        for rule_row in rule_rows:
            rules_payload.append(
                {
                    "rule_id": rule_row.rule_id,
                    "rule_name": rule_row.rule_name,
                    "description": rule_row.description,
                    "category": rule_row.category,
                    "severity": rule_row.severity,
                    "drilldown_fields": parse_json_array(rule_row.drilldown_fields),
                    "required_closure_fields": parse_json_array(rule_row.required_closure_fields),
                    "closure_template": rule_row.closure_template,
                    "enabled": rule_row.enabled,
                }
            )
        return {"rules": rules_payload}
    finally:
        database_session.close()


def build_supportive_payload() -> dict:
    database_session = SessionLocal()
    try:
        query_rows = (
            database_session.query(SupportiveQuery)
            .order_by(SupportiveQuery.rule_id.asc(), SupportiveQuery.title.asc())
            .all()
        )
        grouped_queries: dict[str, list[dict[str, str]]] = {}
        for query_row in query_rows:
            grouped_queries.setdefault(query_row.rule_id, []).append(
                {
                    "title": query_row.title,
                    "description": query_row.description,
                    "spl_query": query_row.spl_query,
                }
            )

        supportive_payload = []
        for rule_id, supportive_queries in grouped_queries.items():
            supportive_payload.append(
                {
                    "rule_id": rule_id,
                    "supportive_queries": supportive_queries,
                }
            )
        return {"rules": supportive_payload}
    finally:
        database_session.close()


def write_json(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export shared logic from DB into reviewable JSON")
    export_dir = default_export_dir()
    parser.add_argument(
        "--rules-output",
        default=str(export_dir / "sample_rules.exported.json"),
        help="Path to write exported rule definitions JSON.",
    )
    parser.add_argument(
        "--supportive-output",
        default=str(export_dir / "supportive_rules.exported.json"),
        help="Path to write exported supportive SPL query JSON.",
    )
    return parser


def main() -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args()

    rules_output = Path(arguments.rules_output)
    supportive_output = Path(arguments.supportive_output)

    rules_payload = build_rules_payload()
    supportive_payload = build_supportive_payload()

    write_json(rules_output, rules_payload)
    write_json(supportive_output, supportive_payload)

    print(f"[+] Exported rules to: {rules_output}")
    print(f"[+] Exported supportive queries to: {supportive_output}")


if __name__ == "__main__":
    main()