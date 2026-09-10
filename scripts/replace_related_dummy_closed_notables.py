#!/usr/bin/env python3
"""Replace historical dummy notables with examples related to current triage cases."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


CASE_CONTEXT = {
    "powershell_in_memory": (
        ["WIN-APP-042", "WIN-APP-043"],
        ["svc_deploy", "admin.jlee"],
        {"Process": r"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", "ParentImage": r"C:\\Windows\\System32\\svchost.exe"},
    ),
    "linux_ngrok": (
        ["linux-bastion-02", "linux-bastion-03"],
        ["svc_platform", "ops.engineer"],
        {"Process": "/usr/local/bin/ngrok", "Destination Port": "443"},
    ),
    "git_child_process": (
        ["DEV-BUILD-07", "DEV-BUILD-08"],
        ["build_runner", "developer1"],
        {"Parent Process": "git.exe", "Child Process": "7z.exe"},
    ),
    "interactive_logon_service_accounts": (
        ["AUTH-JUMP-02", "AUTH-JUMP-03"],
        ["svc_backup", "svc_sql"],
        {"Logon Type": "Interactive", "Source Workstation": "ADMIN-JUMP-01"},
    ),
    "public_bucket_exposure": (
        ["cloud-storage-prod-02", "cloud-storage-prod-03"],
        ["unknown", "external-user"],
        {"Bucket": "soc-platform-evidence", "Destination Port": "443"},
    ),
    "sso_brute_force_pingfederate": (
        ["pingfed-prod-02", "pingfed-prod-03"],
        ["unknown", "unknown"],
        {"Authentication Service": "PingFederate", "Destination Port": "443"},
    ),
}


def _disposition(verdict: str, index: int) -> str:
    verdict = (verdict or "").lower()
    if verdict == "malicious":
        return "True Positive - Malicious Activity" if index == 0 else "Benign Positive - Suspicious But Expected"
    if verdict == "benign":
        return "Benign Positive - Suspicious But Expected"
    return "Suspicious Activity - Requires Investigation" if index == 0 else "False Positive - Incorrect Analytic Logic"


def main() -> int:
    repo_root = os.environ.get("SOC_PLATFORM_ROOT") or os.getcwd()
    os.environ.setdefault("SOC_PLATFORM_ROOT", repo_root)
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://soc_platform@postgres:5432/soc_platform")

    from db.models import SessionLocal, SplunkEvent, TriageResult

    db = SessionLocal()
    try:
        historical = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).all()
        removed = 0
        for event in historical:
            try:
                payload = json.loads(event.raw or "{}")
            except json.JSONDecodeError:
                payload = {}
            if payload.get("historical") is True:
                db.delete(event)
                removed += 1

        cases = db.query(TriageResult).order_by(TriageResult.case_id.asc()).all()
        created = 0
        now = datetime.utcnow()
        for case_index, case in enumerate(cases):
            hosts, users, extras = CASE_CONTEXT.get(
                case.rule_id,
                ([f"{(case.rule_id or 'soc-case').replace('_', '-')}-01", f"{(case.rule_id or 'soc-case').replace('_', '-')}-02"], ["analyst", "svc_security"], {}),
            )
            for example_index in range(2):
                title = case.rule_name or case.rule_id or "SOC Test Detection"
                host = hosts[example_index]
                user = users[example_index]
                disposition = _disposition(case.verdict, example_index)
                timestamp = now - timedelta(hours=case_index * 3 + example_index + 1)
                fields = {
                    "title": title,
                    "correlation_search": title,
                    "rule_id": case.rule_id,
                    "host": host,
                    "user": user,
                    "urgency": "critical" if case.verdict == "malicious" else "high",
                    "disposition": disposition,
                    "time": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                    "Status": "Closed",
                    "Owner": "dummy-analyst",
                    **extras,
                }
                lines = [f"{key}: {value}" for key, value in fields.items()]
                payload = {
                    "record_type": "splunk_notable_paste",
                    "raw_fields": fields,
                    "fields": fields,
                    "sanitized_text": "\n".join(lines) + "\n",
                    "history": "",
                    "parse_assessment": {"ok": True, "source": "related_dummy_seed"},
                    "saved_at": (timestamp - timedelta(minutes=5)).isoformat(),
                    "artifact_paths": {},
                    "historical": True,
                    "segment_index": 0,
                    "segment_count": 1,
                    "dedup_key": f"related-dummy-closed-{case.case_id}-{example_index}",
                }
                db.add(SplunkEvent(
                    sourcetype="splunk:notable:pasted",
                    source=title,
                    host=host,
                    raw=json.dumps(payload),
                    timestamp=timestamp,
                ))
                created += 1

        db.commit()
        print(f"Removed {removed} historical dummy notables.")
        print(f"Created {created} related closed notables for {len(cases)} current triaged cases.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"FATAL: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
