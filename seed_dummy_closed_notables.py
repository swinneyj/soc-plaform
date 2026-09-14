#!/usr/bin/env python3
"""
Insert a small, clean set of *historical* (closed) pasted notables.

These appear under the Closed Notables view and are suitable for testing
delete / details / timeline UI without real production data.

Payload shape matches what /api/db/notables/historical and the details
endpoint expect (fields + historical flag + saved_at).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta


DUMMY_CLOSED = [
    {
        "title": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "correlation_search": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "host": "NDC36-81",
        "user": "local service",
        "urgency": "high",
        "disposition": "Benign Positive - Suspicious But Expected",
        "time": "2026-09-10T02:02:39",
        "extra_fields": {
            "Source IP": "10.24.17.73",
            "Destination IP": "51.55.22.165",
            "Dest Port": "443",
            "Source Port": "53267",
            "Process": r"c:\windows\system32\windowspowershell\v1.0\powershell.exe",
            "Owner": "justin swinney",
            "Status": "Closed",
        },
        "sanitized_text": (
            "Title: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Correlation Search: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Host: NDC36-81\n"
            "User: local service\n"
            "Urgency: high\n"
            "Disposition: Benign Positive - Suspicious But Expected\n"
            "Time: 2026-09-10 02:02:39\n"
            "Source IP: 10.24.17.73\n"
            "Destination IP: 51.55.22.165\n"
            "Dest Port: 443\n"
            "Process: c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe\n"
            "Owner: justin swinney\n"
        ),
    },
    {
        "title": "Endpoint - Linux Possible ssh Key File Creation - Rule",
        "correlation_search": "Endpoint - Linux Possible ssh Key File Creation - Rule",
        "host": "ndc1-184.navair.navy.mil",
        "user": "asimmons",
        "urgency": "medium",
        "disposition": "False Positive - Incorrect Analytic Logic",
        "time": "2026-09-03T16:38:46",
        "extra_fields": {
            "Status": "Closed",
            "Owner": "justin swinney",
        },
        "sanitized_text": (
            "Title: Endpoint - Linux Possible ssh Key File Creation - Rule\n"
            "Correlation Search: Endpoint - Linux Possible ssh Key File Creation - Rule\n"
            "Host: ndc1-184.navair.navy.mil\n"
            "User: asimmons\n"
            "Urgency: medium\n"
            "Disposition: False Positive - Incorrect Analytic Logic\n"
            "Time: 2026-09-03 16:38:46\n"
            "Owner: justin swinney\n"
        ),
    },
    {
        "title": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "correlation_search": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "host": "NDC45-790",
        "user": "rpa_sea.svc",
        "urgency": "high",
        "disposition": "Benign Positive - Suspicious But Expected",
        "time": "2026-09-10T12:03:23",
        "extra_fields": {
            "Source IP": "10.24.10.55",
            "Destination IP": "52.12.88.101",
            "Dest Port": "443",
            "Process": r"c:\windows\system32\windowspowershell\v1.0\powershell.exe",
            "Owner": "justin swinney",
            "Status": "Closed",
        },
        "sanitized_text": (
            "Title: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Correlation Search: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Host: NDC45-790\n"
            "User: rpa_sea.svc\n"
            "Urgency: high\n"
            "Disposition: Benign Positive - Suspicious But Expected\n"
            "Time: 2026-09-10 12:03:23\n"
            "Source IP: 10.24.10.55\n"
            "Destination IP: 52.12.88.101\n"
            "Dest Port: 443\n"
            "Process: c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe\n"
            "Owner: justin swinney\n"
        ),
    },
    {
        "title": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "correlation_search": "Endpoint - Suspicious LotL Process Outbound Connection - Rule",
        "host": "NDC108-3",
        "user": "ian.hattendorf.am",
        "urgency": "critical",
        "disposition": "Benign Positive - Suspicious But Expected",
        "time": "2026-09-09T02:02:17",
        "extra_fields": {
            "Source IP": "10.24.78.205",
            "Destination IP": "140.32.169.110",
            "Dest Port": "443",
            "Source Port": "49390",
            "Process": r"c:\windows\system32\windowspowershell\v1.0\powershell.exe",
            "Owner": "justin swinney",
            "Status": "Closed",
        },
        "sanitized_text": (
            "Title: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Correlation Search: Endpoint - Suspicious LotL Process Outbound Connection - Rule\n"
            "Host: NDC108-3\n"
            "User: ian.hattendorf.am\n"
            "Urgency: critical\n"
            "Disposition: Benign Positive - Suspicious But Expected\n"
            "Time: 2026-09-09 02:02:17\n"
            "Source IP: 10.24.78.205\n"
            "Destination IP: 140.32.169.110\n"
            "Dest Port: 443\n"
            "Process: c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe\n"
            "Owner: justin swinney\n"
        ),
    },
    {
        "title": "SSO Brute Force Success - PingFederate",
        "correlation_search": "SSO Brute Force Success - PingFederate",
        "host": "pingfed-prod-01",
        "user": "unknown",
        "urgency": "critical",
        "disposition": "True Positive - Malicious Activity",
        "time": "2026-09-08T09:15:00",
        "extra_fields": {
            "Source IP": "185.220.101.42",
            "Status": "Closed",
            "Owner": "justin swinney",
        },
        "sanitized_text": (
            "Title: SSO Brute Force Success - PingFederate\n"
            "Correlation Search: SSO Brute Force Success - PingFederate\n"
            "Host: pingfed-prod-01\n"
            "User: unknown\n"
            "Urgency: critical\n"
            "Disposition: True Positive - Malicious Activity\n"
            "Time: 2026-09-08 09:15:00\n"
            "Source IP: 185.220.101.42\n"
            "Owner: justin swinney\n"
        ),
    },
]


def _parse_time(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def main() -> int:
    repo_root = os.environ.get("SOC_PLATFORM_ROOT") or os.getcwd()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if not os.environ.get("SOC_PLATFORM_ROOT"):
        os.environ["SOC_PLATFORM_ROOT"] = repo_root
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = (
            "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
        )

    from db.models import SessionLocal, SplunkEvent

    db = SessionLocal()
    try:
        created = 0
        base_saved = datetime.utcnow()

        for i, item in enumerate(DUMMY_CLOSED):
            fields = {
                "title": item["title"],
                "correlation_search": item["correlation_search"],
                "host": item["host"],
                "user": item["user"],
                "urgency": item["urgency"],
                "disposition": item["disposition"],
                "time": item["time"],
            }
            fields.update(item.get("extra_fields") or {})

            # Stagger saved_at so the UI sort order is stable and realistic
            saved_at = (base_saved - timedelta(minutes=i * 7)).isoformat()

            payload = {
                "record_type": "splunk_notable_paste",
                "raw_fields": fields,
                "fields": fields,
                "sanitized_text": item["sanitized_text"],
                "history": "",
                "parse_assessment": {"ok": True, "source": "dummy_seed"},
                "saved_at": saved_at,
                "artifact_paths": {},
                "historical": True,
                "segment_index": 0,
                "segment_count": 1,
                "dedup_key": f"dummy-closed-{i}-{item['host']}-{item['user']}",
            }

            event = SplunkEvent(
                sourcetype="splunk:notable:pasted",
                source=item["correlation_search"] or item["title"],
                host=item["host"],
                raw=json.dumps(payload),
                timestamp=_parse_time(item["time"]),
            )
            db.add(event)
            created += 1

        db.commit()
        print(f"Created {created} dummy closed (historical) notables.")
        return 0
    except Exception as exc:
        print(f"FATAL: {exc}")
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
