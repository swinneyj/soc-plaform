from datetime import datetime, timedelta
import json
import os
import sys

from db.models import SessionLocal, SplunkEvent, TriageResult


TEST_CASES = [
    {
        "case_id": "TEST-POWERSHELL-1",
        "rule_id": "powershell_in_memory",
        "rule_name": "PowerShell In-Memory Execution",
        "verdict": "malicious",
        "confidence_score": 0.95,
        "analysis_summary": "Simulated in-memory PowerShell run by admin user executing suspicious encoded payload.",
        "remediation_steps": "Confirm script block hashes, isolate host, and reset credentials.",
        "fields": {
            "title": "PowerShell In-Memory Execution",
            "correlation_search": "PowerShell In-Memory Execution",
            "rule_id": "powershell_in_memory",
            "host": "WIN-APP-042",
            "dest": "WIN-APP-042",
            "user": "admin.jlee",
            "src_user": "admin.jlee",
            "process": "powershell.exe",
            "process_name": "powershell.exe",
            "parent_process": "svchost.exe",
            "parent_process_name": "svchost.exe",
            "process_exec": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA...",
            "command_line": r"powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA...",
            "severity": "critical",
            "urgency": "critical",
            "security_domain": "endpoint",
            "Owner": "unassigned",
            "Status": "In Progress",
        },
    },
    {
        "case_id": "TEST-POWERSHELL-2",
        "rule_id": "powershell_in_memory",
        "rule_name": "PowerShell In-Memory Execution",
        "verdict": "benign",
        "confidence_score": 0.7,
        "analysis_summary": "Legitimate in-memory PowerShell usage by deployment automation account.",
        "remediation_steps": "Document exception and ensure monitoring baseline covers this pattern.",
        "fields": {
            "title": "PowerShell In-Memory Execution",
            "correlation_search": "PowerShell In-Memory Execution",
            "rule_id": "powershell_in_memory",
            "host": "WIN-APP-043",
            "dest": "WIN-APP-043",
            "user": "svc_deploy",
            "src_user": "svc_deploy",
            "process": "powershell.exe",
            "process_name": "powershell.exe",
            "parent_process": "cct_agent.exe",
            "parent_process_name": "cct_agent.exe",
            "process_exec": r"powershell.exe -ExecutionPolicy Bypass -File C:\ProgramData\Automation\deploy_agent.ps1",
            "command_line": r"powershell.exe -ExecutionPolicy Bypass -File C:\ProgramData\Automation\deploy_agent.ps1",
            "severity": "low",
            "urgency": "low",
            "security_domain": "endpoint",
            "Owner": "analyst.swinney",
            "Status": "Closed",
        },
    },
    {
        "case_id": "TEST-NGROK-1",
        "rule_id": "linux_ngrok",
        "rule_name": "Linux Ngrok Reverse Proxy Usage",
        "verdict": "suspicious",
        "confidence_score": 0.88,
        "analysis_summary": "Ngrok client observed on Linux bastion host with outbound tunnels.",
        "remediation_steps": "Review outbound ngrok connections, confirm business justification, and block if unauthorized.",
        "fields": {
            "title": "Linux Ngrok Reverse Proxy Usage",
            "correlation_search": "Linux Ngrok Reverse Proxy Usage",
            "rule_id": "linux_ngrok",
            "host": "linux-bastion-02",
            "dest": "linux-bastion-02",
            "user": "ops.engineer",
            "src_user": "ops.engineer",
            "process": "/usr/local/bin/ngrok",
            "process_name": "ngrok",
            "parent_process": "/bin/bash",
            "parent_process_name": "bash",
            "command_line": "/usr/local/bin/ngrok tcp 22 --log=stdout",
            "destination_ip": "198.51.100.45",
            "dest_port": "443",
            "severity": "high",
            "urgency": "high",
            "security_domain": "network",
            "Owner": "unassigned",
            "Status": "New",
        },
    },
    {
        "case_id": "TEST-GIT-CHILD-1",
        "rule_id": "git_child_process",
        "rule_name": "Git Unexpected Child Process",
        "verdict": "suspicious",
        "confidence_score": 0.82,
        "analysis_summary": "Git spawning uncommon child process associated with archive extraction utility.",
        "remediation_steps": "Correlate with developer workflow; if unexpected, investigate user workstation for malware.",
        "fields": {
            "title": "Git Unexpected Child Process",
            "correlation_search": "Git Unexpected Child Process",
            "rule_id": "git_child_process",
            "host": "DEV-BUILD-07",
            "dest": "DEV-BUILD-07",
            "user": "developer1",
            "src_user": "developer1",
            "parent_process": r"C:\Program Files\Git\cmd\git.exe",
            "parent_process_name": "git.exe",
            "process": r"C:\Tools\7z.exe",
            "process_name": "7z.exe",
            "command_line": r"7z.exe x archive.zip -o	emp\extracted",
            "severity": "medium",
            "urgency": "medium",
            "security_domain": "endpoint",
            "Owner": "unassigned",
            "Status": "New",
        },
    },
    {
        "case_id": "TEST-SERVICE-ACCT-1",
        "rule_id": "interactive_logon_service_accounts",
        "rule_name": "Interactive Logon to Service Accounts",
        "verdict": "malicious",
        "confidence_score": 0.9,
        "analysis_summary": "Service account used for interactive logon from non-standard workstation.",
        "remediation_steps": "Disable account, rotate credentials, and review other logon activity.",
        "fields": {
            "title": "Interactive Logon to Service Accounts",
            "correlation_search": "Interactive Logon to Service Accounts",
            "rule_id": "interactive_logon_service_accounts",
            "host": "DC-AUTH-01.corp.internal",
            "dest": "DC-AUTH-01.corp.internal",
            "user": "svc_backup_admin",
            "src_user": "svc_backup_admin",
            "src": "WS-DEV-992",
            "src_ip": "10.200.12.44",
            "dest_ip": "10.100.4.10",
            "logon_type": "Interactive (Type 2)",
            "logon_process": "User32",
            "authentication_package": "Negotiate",
            "severity": "critical",
            "urgency": "critical",
            "security_domain": "identity",
            "Owner": "unassigned",
            "Status": "In Progress",
        },
    },
    {
        "case_id": "TEST-BUCKET-1",
        "rule_id": "public_bucket_exposure",
        "rule_name": "Public Bucket / Storage Exposure",
        "verdict": "malicious",
        "confidence_score": 0.93,
        "analysis_summary": "Publicly exposed storage bucket accessed from foreign IPs.",
        "remediation_steps": "Restrict bucket ACLs, rotate affected keys, and review access logs.",
        "fields": {
            "title": "Public Bucket / Storage Exposure",
            "correlation_search": "Public Bucket / Storage Exposure",
            "rule_id": "public_bucket_exposure",
            "host": "s3-gateway-us-east-1",
            "dest": "s3-gateway-us-east-1",
            "bucket_name": "soc-platform-evidence-prod",
            "user": "anon-storage-crawler",
            "src_user": "anon-storage-crawler",
            "src": "203.0.113.88",
            "source_ip": "203.0.113.88",
            "dest_ip": "10.0.1.50",
            "destination_port": "443",
            "http_method": "GET",
            "uri": "/soc-platform-evidence-prod/database_backup_2026.sql",
            "status_code": "200",
            "severity": "high",
            "urgency": "high",
            "security_domain": "cloud",
            "Owner": "unassigned",
            "Status": "New",
        },
    },
    {
        "case_id": "TEST-PINGFED-1",
        "rule_id": "sso_brute_force_pingfederate",
        "rule_name": "SSO Brute Force Success - PingFederate",
        "verdict": "malicious",
        "confidence_score": 0.97,
        "analysis_summary": "PingFederate logs show password spraying followed by successful authentication.",
        "remediation_steps": "Force password reset, review MFA posture, and block attacking IP ranges.",
        "fields": {
            "title": "SSO Brute Force Success - PingFederate",
            "correlation_search": "SSO Brute Force Success - PingFederate",
            "rule_id": "sso_brute_force_pingfederate",
            "host": "pingfed-prod-01.corp.internal",
            "dest": "pingfed-prod-01.corp.internal",
            "user": "jdoe@corp.internal",
            "src_user": "jdoe@corp.internal",
            "src": "198.51.100.75",
            "source_ip": "198.51.100.75",
            "dest_ip": "10.100.4.15",
            "dest_port": "443",
            "auth_service": "PingFederate",
            "failed_attempts": "42",
            "success_event": "SSO_AUTH_SUCCESS",
            "severity": "critical",
            "urgency": "critical",
            "security_domain": "access",
            "Owner": "unassigned",
            "Status": "In Progress",
        },
    },
]


def seed_test_cases():
    platform_root = os.environ.get("SOC_PLATFORM_ROOT")
    if not platform_root:
        os.environ["SOC_PLATFORM_ROOT"] = os.getcwd()

    db = SessionLocal()
    try:
        cases_created = 0
        events_created = 0
        now = datetime.utcnow()

        for idx, case_def in enumerate(TEST_CASES):
            case_id = case_def["case_id"]
            timestamp = now - timedelta(hours=idx * 2 + 1)
            time_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S")

            # 1. Check / Create TriageResult
            triage = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
            if not triage:
                triage = TriageResult(
                    case_id=case_id,
                    rule_name=case_def["rule_name"],
                    rule_id=case_def["rule_id"],
                    verdict=case_def["verdict"],
                    confidence_score=case_def["confidence_score"],
                    analysis_summary=case_def["analysis_summary"],
                    remediation_steps=case_def["remediation_steps"],
                    triaged_at=timestamp,
                )
                db.add(triage)
                cases_created += 1
            else:
                # Update fields if needed
                triage.rule_name = case_def["rule_name"]
                triage.rule_id = case_def["rule_id"]
                triage.verdict = case_def["verdict"]
                triage.confidence_score = case_def["confidence_score"]

            # 2. Check / Create matching SplunkEvent (source pasted notable)
            fields = dict(case_def["fields"])
            fields["time"] = time_str
            fields["case_id"] = case_id
            lines = [f"{k}: {v}" for k, v in fields.items()]
            sanitized_text = "\n".join(lines) + "\n"

            payload = {
                "record_type": "splunk_notable_paste",
                "promoted_case_id": case_id,
                "promoted_at": timestamp.isoformat(),
                "fields": fields,
                "raw_fields": fields,
                "sanitized_text": sanitized_text,
                "history": f"System promoted test case {case_id} at {timestamp.isoformat()}",
                "parse_assessment": {
                    "ok": True,
                    "score": 95,
                    "mode": "normal",
                    "missing_anchors": [],
                },
                "saved_at": timestamp.isoformat(),
                "historical": False,
                "dedup_key": f"test-seed-{case_id}",
            }

            # Find existing event for this case
            existing_event = None
            for ev in db.query(SplunkEvent).filter(SplunkEvent.sourcetype == "splunk:notable:pasted").all():
                try:
                    p = json.loads(ev.raw) if ev.raw else {}
                    if p.get("promoted_case_id") == case_id:
                        existing_event = ev
                        break
                except Exception:
                    continue

            if not existing_event:
                db.add(SplunkEvent(
                    sourcetype="splunk:notable:pasted",
                    source=case_def["rule_name"],
                    host=fields.get("host") or "unknown",
                    raw=json.dumps(payload),
                    timestamp=timestamp,
                ))
                events_created += 1
            else:
                # Refresh payload
                existing_event.raw = json.dumps(payload)
                existing_event.timestamp = timestamp
                existing_event.source = case_def["rule_name"]
                existing_event.host = fields.get("host") or "unknown"

        db.commit()
        print(f"Seeded {cases_created} cases and {events_created} matching source pasted notables.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_test_cases()
