"""Compare repo-backed shared logic JSON with the current database state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.export_shared_logic_from_db import (  # noqa: E402
    build_rules_payload,
    build_supportive_payload,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def index_rules(payload: dict) -> dict[str, dict]:
    return {item["rule_id"]: item for item in payload.get("rules", [])}


def index_supportive(payload: dict) -> dict[tuple[str, str], dict]:
    indexed_queries: dict[tuple[str, str], dict] = {}
    for rule_entry in payload.get("rules", []):
        rule_id = rule_entry["rule_id"]
        for supportive_query in rule_entry.get("supportive_queries", []):
            indexed_queries[(rule_id, supportive_query["title"])] = supportive_query
    return indexed_queries


def diff_keys(expected_keys: set, actual_keys: set) -> dict[str, list]:
    return {
        "missing_in_db": sorted(expected_keys - actual_keys),
        "extra_in_db": sorted(actual_keys - expected_keys),
    }


def diff_records(expected_records: dict, actual_records: dict) -> list[str]:
    mismatches = []
    for record_key in sorted(set(expected_records).intersection(actual_records)):
        if expected_records[record_key] != actual_records[record_key]:
            mismatches.append(str(record_key))
    return mismatches


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check drift between repo shared logic JSON and DB state")
    parser.add_argument(
        "--rules-file",
        default=str(REPO_ROOT / "sample_rules.json"),
        help="Path to repo-backed rule definition JSON.",
    )
    parser.add_argument(
        "--supportive-file",
        default=str(REPO_ROOT / "supportive_rules.json"),
        help="Path to repo-backed supportive query JSON.",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit with code 1 when drift is detected.",
    )
    return parser


def main() -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args()

    repo_rules = load_json(Path(arguments.rules_file))
    repo_supportive = load_json(Path(arguments.supportive_file))
    db_rules = build_rules_payload()
    db_supportive = build_supportive_payload()

    repo_rule_index = index_rules(repo_rules)
    db_rule_index = index_rules(db_rules)
    repo_supportive_index = index_supportive(repo_supportive)
    db_supportive_index = index_supportive(db_supportive)

    rule_key_diff = diff_keys(set(repo_rule_index), set(db_rule_index))
    supportive_key_diff = diff_keys(set(repo_supportive_index), set(db_supportive_index))
    rule_mismatches = diff_records(repo_rule_index, db_rule_index)
    supportive_mismatches = diff_records(repo_supportive_index, db_supportive_index)

    result = {
        "rules": {
            **rule_key_diff,
            "mismatched_records": rule_mismatches,
        },
        "supportive_queries": {
            **supportive_key_diff,
            "mismatched_records": supportive_mismatches,
        },
    }

    print(json.dumps(result, indent=2))

    has_drift = any(
        [
            rule_key_diff["missing_in_db"],
            rule_key_diff["extra_in_db"],
            rule_mismatches,
            supportive_key_diff["missing_in_db"],
            supportive_key_diff["extra_in_db"],
            supportive_mismatches,
        ]
    )
    if arguments.fail_on_drift and has_drift:
        raise SystemExit(1)


if __name__ == "__main__":
    main()