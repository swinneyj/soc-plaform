import json
import os
from datetime import datetime

from db.models import SessionLocal, SupportiveQueryResult

# Synthetic supportive results for existing TEST-* cases.
# In a real flow, you would parse CSV/JSON exports from Splunk or SQL
# and populate this structure dynamically.

SUPPORTIVE_RESULTS = [
    {
        "case_id": "TEST-POWERSHELL-1",
        "rule_id": "powershell_in_memory",
        "query_title": "Recent in-memory PowerShell by host & user",
        "source_system": "splunk",
        "raw_result": {
            "summary": "5 in-memory PowerShell events for the user on the impacted host in the last 24h.",
            "metrics": {
                "events": 5,
                "distinct_script_hashes": 3
            }
        },
    },
    {
        "case_id": "TEST-POWERSHELL-2",
        "rule_id": "powershell_in_memory",
        "query_title": "Recent in-memory PowerShell by host & user",
        "source_system": "splunk",
        "raw_result": {
            "summary": "1 in-memory PowerShell event associated with a known deployment script.",
            "metrics": {
                "events": 1,
                "distinct_script_hashes": 1
            }
        },
    },
    {
        "case_id": "TEST-NGROK-1",
        "rule_id": "linux_ngrok",
        "query_title": "Ngrok processes on impacted host",
        "source_system": "splunk",
        "raw_result": {
            "summary": "Ngrok client process observed with two active tunnels to external endpoints.",
            "metrics": {
                "processes": 1,
                "tunnels": 2
            }
        },
    },
    {
        "case_id": "TEST-GIT-CHILD-1",
        "rule_id": "git_child_process",
        "query_title": "Git child process lineage",
        "source_system": "splunk",
        "raw_result": {
            "summary": "Git spawned an uncommon child process '7z.exe' used to extract an archive.",
            "metrics": {
                "suspicious_children": 1
            }
        },
    },
    {
        "case_id": "TEST-SERVICE-ACCT-1",
        "rule_id": "interactive_logon_service_accounts",
        "query_title": "Interactive logons by service account",
        "source_system": "splunk",
        "raw_result": {
            "summary": "Service account logged on interactively to two workstations outside the approved list.",
            "metrics": {
                "hosts": 2
            }
        },
    },
    {
        "case_id": "TEST-BUCKET-1",
        "rule_id": "public_bucket_exposure",
        "query_title": "Access patterns for exposed bucket",
        "source_system": "splunk",
        "raw_result": {
            "summary": "Exposed bucket accessed from 4 distinct external IPs, 2 in unexpected geos.",
            "metrics": {
                "distinct_ips": 4,
                "suspicious_geos": 2
            }
        },
    },
    {
        "case_id": "TEST-PINGFED-1",
        "rule_id": "sso_brute_force_pingfederate",
        "query_title": "Source IP spray pattern",
        "source_system": "splunk",
        "raw_result": {
            "summary": "Three source IPs performed spraying; one IP targeted more than 10 distinct users.",
            "metrics": {
                "spraying_ips": 3,
                "high_volume_ips": 1
            }
        },
    },
]


def seed_supportive_results():
    # Ensure SOC_PLATFORM_ROOT is set for db.models if needed
    platform_root = os.environ.get("SOC_PLATFORM_ROOT")
    if not platform_root:
        os.environ["SOC_PLATFORM_ROOT"] = os.getcwd()

    db = SessionLocal()
    try:
        created = 0
        for item in SUPPORTIVE_RESULTS:
            existing = db.query(SupportiveQueryResult).filter(
                SupportiveQueryResult.case_id == item["case_id"],
                SupportiveQueryResult.query_title == item["query_title"],
            ).first()
            if existing:
                continue

            rec = SupportiveQueryResult(
                case_id=item["case_id"],
                rule_id=item["rule_id"],
                query_title=item["query_title"],
                source_system=item.get("source_system", "splunk"),
                raw_result=json.dumps(item["raw_result"]),
                created_at=datetime.utcnow(),
            )
            db.add(rec)
            created += 1

        db.commit()
        print(f"Created {created} supportive query result records.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_supportive_results()
