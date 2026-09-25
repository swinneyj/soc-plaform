#!/usr/bin/env python3
"""
Seed the mock Splunk backend with three deterministic investigation scenarios.

Creates, idempotently:
  - services/mock_splunk/seed/seed_events.jsonl   (the event corpus the
    MockSplunkBackend searches; committed so every dev/test sees the same data)
  - ESCorrelationRule rows for the three scenario rules
  - SupportiveQuery rows (SPL the mock backend can actually execute)
  - TriageResult rows (the demo cases)
  - SupportiveQueryResult rows produced by running each scenario's queries
    through MockSplunkBackend (proves the loop end-to-end)

Usage:
    .venv314/bin/python scripts/seed_mock_splunk.py          # seed
    .venv314/bin/python scripts/seed_mock_splunk.py --reset  # wipe + reseed

Idempotent: rules/queries/cases are matched by natural keys and updated in
place; result rows are replaced per (case_id, query_title). Events regenerate
relative to *now* each run unless SEED_ANCHOR fixes a wall-clock anchor.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import (  # noqa: E402
    ESCorrelationRule,
    SupportiveQuery,
    SupportiveQueryResult,
    TriageResult,
    SessionLocal,
)
from services.search_backend import MockSplunkBackend  # noqa: E402

SEED_DIR = ROOT / "services" / "mock_splunk" / "seed"
SEED_FILE = SEED_DIR / "seed_events.jsonl"

# -------------------------------------------------------------------------
# Scenario definitions (deterministic ground truth)
# -------------------------------------------------------------------------
SCENARIOS = [
    {
        "rule_id": "MOCK-RULE-001",
        "rule_name": "Impossible Travel - VPN Anomaly",
        "description": "User authenticated from two distant geos within minutes.",
        "category": "authentication",
        "severity": "high",
        "case_id": "MOCK-CASE-001",
        "verdict": "suspicious",
        "confidence": 0.72,
        "summary": " VPN geo-velocity violation for user bjones: Virginia then Romania 11 minutes apart.",
        "queries": [
            {
                "title": "Failed logins for user",
                "spl": 'sourcetype=linux_secure action=failure user=bjones | stats count by host',
            },
            {
                "title": "Successful logins by user",
                "spl": 'sourcetype=linux_secure action=success user=bjones',
            },
        ],
    },
    {
        "rule_id": "MOCK-RULE-002",
        "rule_name": "Malware Detection - Suspicious Binary",
        "description": "Known-bad hash written to disk by an office document.",
        "category": "malware",
        "severity": "critical",
        "case_id": "MOCK-CASE-002",
        "verdict": "malicious",
        "confidence": 0.94,
        "summary": "Malicious binary dropped by WINWORD on HOST-FIN-04 and blocked by EDR.",
        "queries": [
            {
                "title": "Malware events by host",
                "spl": 'sourcetype=win_defender_hash | stats count by host',
            },
            {
                "title": "Blocked binaries for case host",
                "spl": 'sourcetype=win_defender_hash action=blocked host=HOST-FIN-04',
            },
        ],
    },
    {
        "rule_id": "MOCK-RULE-003",
        "rule_name": "Data Exfiltration - Large Outbound Transfer",
        "description": "Volume anomaly on outbound HTTPS from a single host.",
        "category": "network",
        "severity": "medium",
        "case_id": "MOCK-CASE-003",
        "verdict": "benign",
        "confidence": 0.61,
        "summary": "Cloud-backup job explains the outbound volume burst on HOST-DEV-02.",
        "queries": [
            {
                "title": "Outbound bytes by host",
                "spl": 'sourcetype=netflow dest_category=external | stats count by host',
            },
            {
                "title": "Backup traffic for case host",
                "spl": 'sourcetype=netflow host=HOST-DEV-02 app=cloud_backup',
            },
        ],
    },
]


def _ip(last_octet: int) -> str:
    return f"10.20.30.{last_octet}"


def _raw_firewall(ts: datetime, action: str, user: str, host: str, ip: str, city: str) -> str:
    return (
        f"{ts.isoformat()} {host} sshd[{1000 + ts.minute}]: "
        f"Accepted/Failed auth action={action} user={user} src_ip={ip} geo={city} app=ssh"
    )


def _raw_defender(ts: datetime, host: str, binary: str, action: str, user: str) -> str:
    return (
        f"{ts.isoformat()} {host} MSDefender: hash={binary} action={action} "
        f"user={user} process=WINWORD.EXE path=C:\\\\Users\\\\Public\\\\{binary}"
    )


def _raw_netflow(ts: datetime, host: str, dest: str, app: str, mb: int, dest_cat: str) -> str:
    return (
        f"{ts.isoformat()} {host} flowd: app={app} dest={dest} dest_category={dest_cat} "
        f"bytes={mb * 1_000_000} duration_s={60 + ts.second}"
    )


def build_events(anchor: datetime) -> list:
    """Deterministic corpus; anchor is 'now' at generation time."""
    events: list = []

    def add(source, sourcetype, host, ts, raw):
        events.append(
            {
                "source": source,
                "sourcetype": sourcetype,
                "host": host,
                "timestamp": ts.isoformat(),
                "raw": raw,
            }
        )

    # Scenario 1: bjones logins — failures on one host, success from far geo
    for i in range(6):
        ts = anchor - timedelta(hours=5, minutes=30 - i * 5)
        add(
            "/var/log/secure", "linux_secure", "VPN-GW-01", ts,
            _raw_firewall(ts, "failure", "bjones", "VPN-GW-01", _ip(101), "Ashburn"),
        )
    ts = anchor - timedelta(hours=5, minutes=19)
    add(
        "/var/log/secure", "linux_secure", "VPN-GW-01", ts,
        _raw_firewall(ts, "success", "bjones", "VPN-GW-01", _ip(77), "Bucharest"),
    )

    # Scenario 2: defender blocked binaries on the finance host
    for i, (binary, user) in enumerate(
        [
            ("invoice_macro.bin", "finuser1"),
            ("invoice_macro.bin", "finuser1"),
            ("payload_stage2.dll", "finuser1"),
        ]
    ):
        ts = anchor - timedelta(hours=3, minutes=47 - i * 7)
        add(
            "MSWinEventLog", "win_defender_hash", "HOST-FIN-04", ts,
            _raw_defender(ts, "HOST-FIN-04", binary, "blocked", user),
        )

    # Scenario 3: netflow burst = cloud backup (benign), plus external noise
    for i in range(8):
        ts = anchor - timedelta(hours=12, minutes=55 - i * 6)
        add(
            "netflow", "netflow", "HOST-DEV-02", ts,
            _raw_netflow(ts, "HOST-DEV-02", "backup.vendor.com", "cloud_backup", 400 + i * 50, "external"),
        )
    ts = anchor - timedelta(hours=11, minutes=5)
    add(
        "netflow", "netflow", "HOST-DEV-02", ts,
        _raw_netflow(ts, "HOST-DEV-02", "cdn.example.net", "browser", 90, "external"),
    )

    # Sparse unrelated traffic so searches prove they actually filter
    for i in range(3):
        ts = anchor - timedelta(hours=2, minutes=20 - i * 4)
        add(
            "/var/log/secure", "linux_secure", "VPN-GW-01", ts,
            _raw_firewall(ts, "failure", "asmith", "VPN-GW-01", _ip(202), "Ashburn"),
        )

    return events


def write_seed_file(anchor: datetime) -> int:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    events = build_events(anchor)
    with open(SEED_FILE, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return len(events)


def seed_db(backend: MockSplunkBackend, reset: bool) -> dict:
    session = SessionLocal()
    stats = {"rules": 0, "queries": 0, "cases": 0, "results": 0}

    try:
        if reset:
            session.query(SupportiveQueryResult).delete()
            session.query(SupportiveQuery).delete()
            session.query(TriageResult).delete()
            session.query(ESCorrelationRule).filter(
                ESCorrelationRule.rule_id.like("MOCK-%")
            ).delete()

        for scenario in SCENARIOS:
            rule = (
                session.query(ESCorrelationRule)
                .filter(ESCorrelationRule.rule_id == scenario["rule_id"])
                .first()
            )
            if rule is None:
                rule = ESCorrelationRule(
                    rule_id=scenario["rule_id"],
                    rule_name=scenario["rule_name"],
                    description=scenario["description"],
                    category=scenario["category"],
                    severity=scenario["severity"],
                    drilldown_fields=json.dumps(["user", "host"]),
                    required_closure_fields=json.dumps(["user", "host"]),
                    enabled=1,
                )
                session.add(rule)
                stats["rules"] += 1
            else:
                rule.rule_name = scenario["rule_name"]
                rule.description = scenario["description"]
                rule.category = scenario["category"]
                rule.severity = scenario["severity"]

            for query_def in scenario["queries"]:
                existing = (
                    session.query(SupportiveQuery)
                    .filter(
                        SupportiveQuery.rule_id == scenario["rule_id"],
                        SupportiveQuery.title == query_def["title"],
                    )
                    .first()
                )
                if existing is None:
                    session.add(
                        SupportiveQuery(
                            rule_id=scenario["rule_id"],
                            title=query_def["title"],
                            description="",
                            spl_query=query_def["spl"],
                        )
                    )
                    stats["queries"] += 1
                elif existing.spl_query != query_def["spl"]:
                    existing.spl_query = query_def["spl"]

            case = (
                session.query(TriageResult)
                .filter(TriageResult.case_id == scenario["case_id"])
                .first()
            )
            if case is None:
                session.add(
                    TriageResult(
                        case_id=scenario["case_id"],
                        rule_name=scenario["rule_name"],
                        rule_id=scenario["rule_id"],
                        verdict=scenario["verdict"],
                        confidence_score=scenario["confidence"],
                        analysis_summary=scenario["summary"],
                    )
                )
                stats["cases"] += 1

            # Run the queries through the mock backend and persist results
            for query_def in scenario["queries"]:
                result = backend.execute_for_case(
                    case_id=scenario["case_id"],
                    rule_id=scenario["rule_id"],
                    query_title=query_def["title"],
                    spl=query_def["spl"],
                    earliest="-7d",
                )
                session.query(SupportiveQueryResult).filter(
                    SupportiveQueryResult.case_id == scenario["case_id"],
                    SupportiveQueryResult.query_title == query_def["title"],
                ).delete()
                session.add(SupportiveQueryResult(**result))
                stats["results"] += 1

        session.commit()
        return stats
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete existing mock rows first")
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="reuse the committed seed_events.jsonl instead of regenerating it",
    )
    args = parser.parse_args()

    anchor_env = os.environ.get("SEED_ANCHOR")
    anchor = datetime.fromisoformat(anchor_env) if anchor_env else datetime.now()

    if not args.no_events:
        count = write_seed_file(anchor)
        print(f"wrote {count} events -> {SEED_FILE.relative_to(ROOT)}")

    backend = MockSplunkBackend()
    stats = seed_db(backend, reset=args.reset)
    print("seeded:", stats)
    print("smoke test:")
    for scenario in SCENARIOS:
        for query_def in scenario["queries"]:
            rows = backend.search(query_def["spl"], earliest="-7d")
            print(f"  [{scenario['case_id']}] {query_def['title']}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
