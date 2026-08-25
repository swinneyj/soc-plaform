from datetime import datetime
import os

from db.models import SessionLocal, TriageResult


TEST_CASES = [
    {
        "case_id": "TEST-POWERSHELL-1",
        "rule_id": "powershell_in_memory",
        "rule_name": "PowerShell In-Memory Execution",
        "verdict": "malicious",
        "confidence_score": 0.95,
        "analysis_summary": "Simulated in-memory PowerShell run by admin user executing suspicious encoded payload.",
        "remediation_steps": "Confirm script block hashes, isolate host, and reset credentials.",
    },
    {
        "case_id": "TEST-POWERSHELL-2",
        "rule_id": "powershell_in_memory",
        "rule_name": "PowerShell In-Memory Execution",
        "verdict": "benign",
        "confidence_score": 0.7,
        "analysis_summary": "Legitimate in-memory PowerShell usage by deployment automation account.",
        "remediation_steps": "Document exception and ensure monitoring baseline covers this pattern.",
    },
    {
        "case_id": "TEST-NGROK-1",
        "rule_id": "linux_ngrok",
        "rule_name": "Linux Ngrok Reverse Proxy Usage",
        "verdict": "suspicious",
        "confidence_score": 0.88,
        "analysis_summary": "Ngrok client observed on Linux bastion host with outbound tunnels.",
        "remediation_steps": "Review outbound ngrok connections, confirm business justification, and block if unauthorized.",
    },
    {
        "case_id": "TEST-GIT-CHILD-1",
        "rule_id": "git_child_process",
        "rule_name": "Git Unexpected Child Process",
        "verdict": "suspicious",
        "confidence_score": 0.82,
        "analysis_summary": "Git spawning uncommon child process associated with archive extraction utility.",
        "remediation_steps": "Correlate with developer workflow; if unexpected, investigate user workstation for malware.",
    },
    {
        "case_id": "TEST-SERVICE-ACCT-1",
        "rule_id": "interactive_logon_service_accounts",
        "rule_name": "Interactive Logon to Service Accounts",
        "verdict": "malicious",
        "confidence_score": 0.9,
        "analysis_summary": "Service account used for interactive logon from non-standard workstation.",
        "remediation_steps": "Disable account, rotate credentials, and review other logon activity.",
    },
    {
        "case_id": "TEST-BUCKET-1",
        "rule_id": "public_bucket_exposure",
        "rule_name": "Public Bucket / Storage Exposure",
        "verdict": "malicious",
        "confidence_score": 0.93,
        "analysis_summary": "Publicly exposed storage bucket accessed from foreign IPs.",
        "remediation_steps": "Restrict bucket ACLs, rotate affected keys, and review access logs.",
    },
    {
        "case_id": "TEST-PINGFED-1",
        "rule_id": "sso_brute_force_pingfederate",
        "rule_name": "SSO Brute Force Success - PingFederate",
        "verdict": "malicious",
        "confidence_score": 0.97,
        "analysis_summary": "PingFederate logs show password spraying followed by successful authentication.",
        "remediation_steps": "Force password reset, review MFA posture, and block attacking IP ranges.",
    },
]


def seed_test_cases():
    # Ensure SOC_PLATFORM_ROOT is set for db.models if needed
    platform_root = os.environ.get("SOC_PLATFORM_ROOT")
    if not platform_root:
        # Best-effort default to current working directory
        os.environ["SOC_PLATFORM_ROOT"] = os.getcwd()

    db = SessionLocal()
    try:
        created = 0
        for case in TEST_CASES:
            existing = db.query(TriageResult).filter(TriageResult.case_id == case["case_id"]).first()
            if existing:
                continue

            triage = TriageResult(
                case_id=case["case_id"],
                rule_name=case["rule_name"],
                rule_id=case["rule_id"],
                verdict=case["verdict"],
                confidence_score=case["confidence_score"],
                analysis_summary=case["analysis_summary"],
                remediation_steps=case["remediation_steps"],
                triaged_at=datetime.utcnow(),
            )
            db.add(triage)
            created += 1

        db.commit()
        print(f"Created {created} test triage cases.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_test_cases()
