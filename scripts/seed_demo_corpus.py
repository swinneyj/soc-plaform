#!/usr/bin/env python3
"""Generate a narrative demo corpus for the fake Splunk backend.

One coherent incident, told in ~500 events across several indexes, anchored to
**now** so the platform's default search windows (-2h, -7d) actually return
rows:

  index=wineventlog  Windows security log  (4624 logons, 4688 process starts)
  index=edr          EDR telemetry         (process lineage, file writes, registry)
  index=proxy        Web proxy             (beacon GETs, exfil POST, benign traffic)
  index=netflow      Network flows         (outbound volume, internal SMB)
  index=auth         VPN authentication    (failures, then a geo-impossible success)
  index=itsm         Change records        (approved changes for *other* hosts)
  index=notable      ES notables           (the alert that starts the case)

The story: a finance workstation opens a macro document, spawns encoded
PowerShell, beacons out, harvests credentials, moves laterally, and the same
user then logs in over VPN from a new geo. The corpus includes plausible
baseline noise on other hosts, so queries filtered by `$host$`/`$user$` must
actually filter — and a "no approved change" query legitimately returns zero.

Outputs (written relative to the repo):
  services/mock_splunk/seed/demo_events.jsonl   the corpus
  services/mock_splunk/seed/demo_notable.txt    paste-ready notable (Label: value)

With ``--db`` it also upserts the demo correlation rule and the six playbook
queries (idempotent, `DEMO-` marked). The case itself is created by the normal
paste -> promote flow, so nothing here invents a triage row.

Usage:
    python scripts/seed_demo_corpus.py                 # corpus + notable
    python scripts/seed_demo_corpus.py --db            # + rule and playbooks
    python scripts/seed_demo_corpus.py --out-dir /tmp/demo

Then:
    python scripts/fake_splunk.py --seed services/mock_splunk/seed/demo_events.jsonl
    SEARCH_BACKEND=splunk SPLUNK_URL=http://127.0.0.1:18089 SPLUNK_TOKEN=dev-token \
      bash scripts/start
    # paste demo_notable.txt into the UI's notable box, promote, run cards

Every event is deterministic for a given ``--seed`` and ``--anchor``.
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED_DIR = ROOT / "services" / "mock_splunk" / "seed"
TARGET_EVENTS = 500

# Identifiers are deliberately hyphen-free: the paste pipeline's default
# redaction tokenizes every `word-word` token (FIN-WS-07 -> HOST_6), which would
# rewrite the very fields $host$/$user$ substitute from. Underscores and plain
# alphanumerics survive with redaction left on, so the demo works out of the box.
DEMO_RULE_ID = "DEMO_RULE_PS_EXFIL"
DEMO_RULE_NAME = "Encoded PowerShell Followed by Unusual VPN Login"
CASE_HOST = "FINWS07"
CASE_USER = "mrivera"
CASE_IP = "10.20.30.101"
BAD_GEO = "Bucharest"
BAD_IP = "185.220.101.7"
C2_HOST = "cdn-sync.example.net"

BASELINE_HOSTS = [
    "FIN-WS-01", "FIN-WS-02", "FIN-WS-05", "ENG-WS-03", "ENG-WS-04",
    "HR-WS-02", "OPS-WS-08", "DC-01", "FS-01", "WEB-01",
]
BASELINE_USERS = [
    "asmith", "tnguyen", "pkumar", "sokafor", "jchen", "rpatel",
    "lgarcia", "dwilliams", "mfischer",
]
GOOD_GEOS = ["Ashburn", "Reston"]
BENIGN_DESTS = [
    "office365.com", "teams.microsoft.com", "outlook.office365.com",
    "update.microsoft.com", "intranet.corp.example", "sharepoint.corp.example",
]
BENIGN_PROCESSES = [
    ("excel.exe", "explorer.exe"), ("chrome.exe", "explorer.exe"),
    ("outlook.exe", "explorer.exe"), ("teams.exe", "explorer.exe"),
    ("onedrive.exe", "explorer.exe"),
]

# Playbook cards. Every SPL stays inside MockSplunkBackend's bounded subset:
# base terms, | search, | where, | stats count by <field>, | head N.
PLAYBOOK_QUERIES = [
    {
        "title": "Encoded PowerShell lineage on case host",
        "description": "Which process spawned PowerShell on the affected endpoint?",
        "spl": "index=edr sourcetype=edr:process host=$host$ process=powershell.exe | stats count by parent_process",
    },
    {
        "title": "Outbound destinations for case host",
        "description": "Where did the endpoint talk on the wire?",
        "spl": "index=proxy host=$host$ | stats count by dest",
    },
    {
        "title": "VPN logon history for case user",
        "description": "Which geographies has this account authenticated from?",
        "spl": "index=auth user=$user$ action=success | stats count by geo",
    },
    {
        "title": "Lateral logons from case host",
        "description": "Did the endpoint authenticate to other hosts?",
        "spl": "index=wineventlog host=$host$ EventCode=4624 logon_type=3 | stats count by dest_host",
    },
    {
        "title": "Malware hashes seen on case host",
        "description": "Which file hashes were written by this endpoint?",
        "spl": "index=edr sourcetype=edr:file host=$host$ | stats count by file_hash",
    },
    {
        "title": "Approved change records for case host",
        "description": "Was this activity covered by an approved change?",
        "spl": "index=itsm host=$host$ status=approved | head 20",
    },
]


def _kv(key, value):
    """Render one `key=value` token, quoting values Splunk would split."""
    text = str(value)
    if '"' in text:
        text = text.replace('"', "'")
    if " " in text or "=" in text or not text:
        return f'{key}="{text}"'
    return f"{key}={text}"


def _ev(index, sourcetype, host, ts, message, source=None, **fields):
    raw_parts = [_kv(k, v) for k, v in fields.items() if v not in (None, "")]
    if message:
        raw_parts.append(message)
    return {
        "index": index,
        "source": source or _default_source(index, sourcetype),
        "sourcetype": sourcetype,
        "host": host,
        "timestamp": ts.isoformat(),
        "raw": " ".join(raw_parts),
    }


def _default_source(index, sourcetype):
    return {
        "wineventlog": "WinEventLog:Security",
        "edr": "EDR",
        "proxy": "squid",
        "netflow": "flowd",
        "auth": "VPN-GW-01",
        "itsm": "ServiceNow",
        "notable": "SplunkEnterpriseSecuritySuite",
    }.get(index, index)


# ---------------------------------------------------------------------------
# The incident: ~85 events over the last 100 minutes
# ---------------------------------------------------------------------------

def incident_events(anchor, rng):
    t = lambda minutes, seconds=0: anchor - timedelta(minutes=minutes, seconds=seconds)
    out = []

    # 1. Delivery: a macro document is opened on the finance workstation.
    out.append(_ev(
        "edr", "edr:process", CASE_HOST, t(100),
        "WINWORD.EXE opened an attachment from an external sender",
        process="WINWORD.EXE", parent_process="explorer.exe", user=CASE_USER,
        file_name="Invoice_Q4.docm", sender="billing@vendorpays.example",
    ))

    # 2. Execution: Word spawns hidden, encoded PowerShell.
    encoded = "SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkA"
    for offset in (98, 97):
        out.append(_ev(
            "edr", "edr:process", CASE_HOST, t(offset),
            "Process creation observed by EDR",
            process="powershell.exe", parent_process="WINWORD.EXE", user=CASE_USER,
            command_line=f"powershell.exe -NoP -NonI -W Hidden -Enc {encoded}",
        ))
    out.append(_ev(
        "wineventlog", "WinEventLog:Security", CASE_HOST, t(98),
        "A new process has been created",
        EventCode=4688, user=CASE_USER, process="powershell.exe",
        parent_process="WINWORD.EXE", logon_type=2,
    ))

    # 3. Staging: payload written, persistence registered.
    out.append(_ev(
        "edr", "edr:file", CASE_HOST, t(96),
        "File write to a user-writable temp path",
        user=CASE_USER, file_name="invoice.dll",
        file_path="C:\\Users\\mrivera\\AppData\\Local\\Temp\\invoice.dll",
        file_hash="9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    ))
    out.append(_ev(
        "edr", "edr:registry", CASE_HOST, t(95),
        "Run key value written for persistence",
        user=CASE_USER, registry_key="HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
        registry_value="OneDriveSync", command_line="rundll32.exe invoice.dll,Start",
    ))

    # 4. Credential access against LSASS.
    out.append(_ev(
        "edr", "edr:process", CASE_HOST, t(88),
        "LSASS handle requested by a non-system process",
        process="rundll32.exe", parent_process="powershell.exe",
        target="lsass.exe", access="0x1010", user=CASE_USER,
    ))

    # 5. C2 beaconing every two minutes, with realistic small payloads.
    for i in range(24):
        out.append(_ev(
            "proxy", "proxy:http", CASE_HOST, t(90 - i * 2, 30),
            "CONNECT to external host",
            user=CASE_USER, dest=C2_HOST, dest_category="uncategorized",
            method="GET", bytes_out=812 + (i * 7), bytes_in=1400 + (i * 11),
            uri="/api/v2/ping",
        ))

    # 6. Hands-on-keyboard children, so lineage stats have texture.
    for i, proc in enumerate(["cmd.exe", "net.exe", "rundll32.exe", "7z.exe", "whoami.exe"]):
        out.append(_ev(
            "edr", "edr:process", CASE_HOST, t(84 - i * 6),
            "Child process of the encoded PowerShell session",
            process=proc, parent_process="powershell.exe", user=CASE_USER,
        ))

    # 7. Lateral movement: type-3 (network) logons to file and domain servers.
    for i in range(8):
        dest = "FS-01" if i % 2 == 0 else "DC-01"
        out.append(_ev(
            "wineventlog", "WinEventLog:Security", CASE_HOST, t(70 - i * 3),
            "An account was successfully logged on",
            EventCode=4624, logon_type=3, user=CASE_USER, dest_host=dest,
        ))

    # 8. Benign browsing from the same host — the destination stats must not be
    #    all-suspicious, or the card proves nothing.
    for i in range(10):
        dest = BENIGN_DESTS[i % len(BENIGN_DESTS)]
        out.append(_ev(
            "proxy", "proxy:http", CASE_HOST, t(66 - i * 4),
            "Browsing traffic",
            user=CASE_USER, dest=dest, dest_category="business",
            method="GET", bytes_out=1200 + i * 13, bytes_in=8000 + i * 250,
            uri="/",
        ))

    # 9. Geo-impossible authentication: failures from the office, then a
    #    success from Bucharest on an unmanaged device.
    for i in range(9):
        out.append(_ev(
            "auth", "vpn:auth", "VPN-GW-01", t(45, -i * 20),
            "Authentication failed",
            action="failure", user=CASE_USER, src_ip=CASE_IP, geo="Ashburn",
            device="managed-laptop", reason="invalid_password",
        ))
    out.append(_ev(
        "auth", "vpn:auth", "VPN-GW-01", t(40),
        "Authentication succeeded",
        action="success", user=CASE_USER, src_ip=BAD_IP, geo=BAD_GEO,
        device="unmanaged", reason="mfa_push_approved",
    ))

    # 10. Exfiltration.
    out.append(_ev(
        "proxy", "proxy:http", CASE_HOST, t(38),
        "Large POST to an external host",
        user=CASE_USER, dest=C2_HOST, dest_category="uncategorized",
        method="POST", bytes_out=42_000_000, bytes_in=2048, uri="/api/v2/upload",
    ))
    out.append(_ev(
        "netflow", "netflow", CASE_HOST, t(36),
        "Outbound flow with anomalous byte count",
        dest=BAD_IP, dest_category="external", app="https",
        bytes=2_100_000_000, duration_s=612,
    ))

    # 11. Detection + the notable.
    out.append(_ev(
        "edr", "edr:alert", CASE_HOST, t(22),
        "EDR behavioral detection: encoded PowerShell with network callback",
        alert="EncodedPowerShell", severity="high", user=CASE_USER,
        process="powershell.exe",
    ))
    out.append(_ev(
        "notable", "notable", CASE_HOST, t(20),
        "Notable event generated by correlation search",
        search_name=DEMO_RULE_NAME, rule_id=DEMO_RULE_ID, severity="high",
        urgency="high", status="New", user=CASE_USER, dest=CASE_HOST,
        src_ip=BAD_IP, dest_ip=CASE_IP, count=1,
        category="endpoint",
        description="Encoded PowerShell from WINWORD followed by an unmanaged VPN login from a new geography.",
    ))

    out.sort(key=lambda event: event["timestamp"])
    return out


# ---------------------------------------------------------------------------
# Baseline noise: plausible activity on *other* hosts, so $host$/$user$
# filters have something real to exclude.
# ---------------------------------------------------------------------------

def _baseline_ts(anchor, rng):
    """Uniformly inside the last seven days, ending two hours before now."""
    return anchor - timedelta(seconds=rng.randint(2 * 3600, 7 * 24 * 3600))


def _baseline_wineventlog(anchor, count, rng):
    for _ in range(count):
        host = rng.choice(BASELINE_HOSTS)
        user = rng.choice(BASELINE_USERS)
        ts = _baseline_ts(anchor, rng)
        if rng.random() < 0.55:
            yield _ev(
                "wineventlog", "WinEventLog:Security", host, ts,
                "An account was successfully logged on",
                EventCode=4624, logon_type=rng.choice([2, 3, 10]),
                user=user, dest_host=host,
            )
        else:
            proc, parent = rng.choice(BENIGN_PROCESSES)
            yield _ev(
                "wineventlog", "WinEventLog:Security", host, ts,
                "A new process has been created",
                EventCode=4688, logon_type=2, user=user,
                process=proc, parent_process=parent,
            )


def _baseline_edr(anchor, count, rng):
    for _ in range(count):
        host = rng.choice(BASELINE_HOSTS)
        user = rng.choice(BASELINE_USERS)
        ts = _baseline_ts(anchor, rng)
        if rng.random() < 0.7:
            proc, parent = rng.choice(BENIGN_PROCESSES)
            yield _ev(
                "edr", "edr:process", host, ts, "Process start observed by EDR",
                process=proc, parent_process=parent, user=user,
            )
        else:
            yield _ev(
                "edr", "edr:file", host, ts, "File write observed by EDR",
                user=user, file_name="document.docx",
                file_path="C:\\Users\\Public\\document.docx",
                file_hash=f"{rng.getrandbits(64):016x}{rng.getrandbits(64):016x}",
            )


def _baseline_proxy(anchor, count, rng):
    for _ in range(count):
        host = rng.choice(BASELINE_HOSTS)
        user = rng.choice(BASELINE_USERS)
        dest = rng.choice(BENIGN_DESTS)
        category = "update" if "microsoft" in dest else "business"
        yield _ev(
            "proxy", "proxy:http", host, _baseline_ts(anchor, rng),
            "Browsing traffic",
            user=user, dest=dest, dest_category=category, method="GET",
            bytes_out=rng.randint(600, 9000), bytes_in=rng.randint(4000, 900000),
            uri="/",
        )


def _baseline_netflow(anchor, count, rng):
    for _ in range(count):
        host = rng.choice(BASELINE_HOSTS)
        internal = rng.random() < 0.45
        dest = rng.choice(["FS-01", "DC-01"]) if internal else rng.choice(
            ["update.microsoft.com", "backup.vendor.example", "intranet.corp.example"]
        )
        yield _ev(
            "netflow", "netflow", host, _baseline_ts(anchor, rng),
            "Flow record",
            dest=dest, dest_category="internal" if internal else "external",
            app="smb" if internal else "https",
            bytes=rng.randint(40_000, 8_000_000),
            duration_s=rng.randint(5, 900),
        )


def _baseline_auth(anchor, count, rng):
    for _ in range(count):
        yield _ev(
            "auth", "vpn:auth", "VPN-GW-01", _baseline_ts(anchor, rng),
            "Authentication succeeded",
            action="success", user=rng.choice(BASELINE_USERS),
            src_ip=f"10.20.30.{rng.randint(10, 250)}",
            geo=rng.choice(GOOD_GEOS), device="managed-laptop",
            reason="mfa_push_approved",
        )


def baseline_events(anchor, count, rng):
    """Exactly ``count`` events, guaranteed ones first (they are the texture
    the playbook cards depend on)."""
    out = []

    # The case user's normal VPN history: Ashburn/Reston only, so the single
    # Bucharest success stands out in `stats count by geo`.
    for i in range(6):
        ts = anchor - timedelta(
            days=1 + i, hours=rng.randint(0, 6), minutes=rng.randint(0, 59)
        )
        out.append(_ev(
            "auth", "vpn:auth", "VPN-GW-01", ts, "Authentication succeeded",
            action="success", user=CASE_USER, src_ip=CASE_IP,
            geo=GOOD_GEOS[i % len(GOOD_GEOS)], device="managed-laptop",
            reason="mfa_push_approved",
        ))

    # Change records exist, but never for the case host — the card that asks
    # "was this an approved change?" must come back empty.
    changes = [
        ("FIN-WS-05", "approved", "Monthly patch window", "CHG0041201"),
        ("FIN-WS-05", "approved", "Backup agent rollout", "CHG0041188"),
        ("FS-01", "approved", "File server capacity expansion", "CHG0041192"),
        ("ENG-WS-04", "rejected", "Unscheduled driver update", "CHG0041205"),
    ]
    for i, (host, status, purpose, change_id) in enumerate(changes):
        out.append(_ev(
            "itsm", "change:request", host, anchor - timedelta(days=2 + i),
            "Change record",
            status=status, change_id=change_id, purpose=purpose,
            user="changemanager",
        ))

    remaining = max(0, count - len(out))
    if remaining:
        builders = [
            (_baseline_wineventlog, int(remaining * 0.34)),
            (_baseline_edr, int(remaining * 0.25)),
            (_baseline_proxy, int(remaining * 0.21)),
            (_baseline_netflow, int(remaining * 0.13)),
            (_baseline_auth, int(remaining * 0.07)),
        ]
        for builder, n in builders:
            out.extend(builder(anchor, n, rng))
        # Rounding dust: top up with the most common sourcetype.
        while len(out) < count:
            out.extend(_baseline_wineventlog(anchor, 1, rng))

    return out[:count]


def build_notable(anchor):
    """The paste-ready notable block (Label: value, one per line).

    This is what an analyst would copy out of Splunk; `parse_structured_notable`
    maps each label onto a placeholder source ($host$, $user$, ...).
    """
    incident_time = (anchor - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    lines = [
        f"Title: {DEMO_RULE_NAME}",
        f"Correlation Search: {DEMO_RULE_NAME}",
        f"Description: Encoded PowerShell launched by WINWORD.EXE on {CASE_HOST}, "
        "followed by a burst of failed logons and an unmanaged VPN success for "
        f"{CASE_USER} from {BAD_GEO}.",
        f"Host: {CASE_HOST}",
        f"Destination: {CASE_HOST}",
        f"User: {CASE_USER}",
        f"Source IP Address: {BAD_IP}",
        "Process: powershell.exe",
        "Parent Process: WINWORD.EXE",
        "Command Line: powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA",
        "Severity: high",
        "Urgency: high",
        "Status: New",
        "Owner: unassigned",
        "Security Domain: endpoint",
        f"Time: {incident_time}",
        "Count: 1",
        f"Rule ID: {DEMO_RULE_ID}",
        "Type: notable",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optional DB seeding (rule + playbooks only — cases come from paste/promote)
# ---------------------------------------------------------------------------

def _db_target_label():
    """Host:port of the configured database. Never the credentials."""
    from urllib.parse import urlparse

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return "unset (platform default)"
    try:
        parsed = urlparse(url)
        return f"{parsed.hostname or '?'}:{parsed.port or 'default'}"
    except ValueError:
        return "unparseable DATABASE_URL"


def seed_db(queries):
    from db.models import ESCorrelationRule, SessionLocal, SupportiveQuery

    print(f"database: {_db_target_label()}")
    session = SessionLocal()
    try:
        rule = (
            session.query(ESCorrelationRule)
            .filter(ESCorrelationRule.rule_id == DEMO_RULE_ID)
            .first()
        )
        if rule is None:
            rule = ESCorrelationRule(
                rule_id=DEMO_RULE_ID,
                rule_name=DEMO_RULE_NAME,                        description="Demo corpus incident: macro -> encoded PowerShell -> C2 -> impossible-travel VPN login.",
                category="endpoint",
                severity="high",
                drilldown_fields=json.dumps(["host", "user"]),
                required_closure_fields=json.dumps(["host", "user"]),
                enabled=1,
            )
            session.add(rule)
        else:
            rule.rule_name = DEMO_RULE_NAME
            rule.enabled = 1

        added = 0
        for query in queries:
            existing = (
                session.query(SupportiveQuery)
                .filter(
                    SupportiveQuery.rule_id == DEMO_RULE_ID,
                    SupportiveQuery.title == query["title"],
                )
                .first()
            )
            if existing is None:
                session.add(SupportiveQuery(
                    rule_id=DEMO_RULE_ID,
                    title=query["title"],
                    description=query["description"],
                    spl_query=query["spl"],
                ))
                added += 1
            else:
                existing.description = query["description"]
                existing.spl_query = query["spl"]

        session.commit()
        print(f"seeded rule {DEMO_RULE_ID} + {len(queries)} playbook queries ({added} new)")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate the narrative fake-Splunk demo corpus (500 events)."
    )
    parser.add_argument("--anchor", default=None,
                        help="ISO timestamp to anchor 'now' to (default: now)")
    parser.add_argument("--seed", type=int, default=1337, help="RNG seed (default 1337)")
    parser.add_argument("--out-dir", default=str(SEED_DIR),
                        help="where to write demo_events.jsonl / demo_notable.txt")
    parser.add_argument("--db", action="store_true",
                        help="also upsert the demo rule + playbook queries in the configured DB")
    args = parser.parse_args(argv)

    anchor = (
        datetime.fromisoformat(args.anchor) if args.anchor
        else datetime.now()
    ).replace(microsecond=0)
    rng = random.Random(args.seed)

    incident = incident_events(anchor, rng)
    baseline = baseline_events(anchor, TARGET_EVENTS - len(incident), rng)
    events = sorted(incident + baseline, key=lambda event: event["timestamp"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "demo_events.jsonl"
    notable_path = out_dir / "demo_notable.txt"

    with open(events_path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    notable_path.write_text(build_notable(anchor) + "\n", encoding="utf-8")

    by_index = {}
    for event in events:
        by_index[event["index"]] = by_index.get(event["index"], 0) + 1

    manifest = {
        "generated_at": anchor.isoformat(),
        "events": len(events),
        "by_index": dict(sorted(by_index.items())),
        "window": {"earliest": events[0]["timestamp"], "latest": events[-1]["timestamp"]},
        "notable": notable_path.name,
        "rule_id": DEMO_RULE_ID,
        "rule_name": DEMO_RULE_NAME,
        "playbook_queries": [query["title"] for query in PLAYBOOK_QUERIES],
        "regenerate_with": "python scripts/seed_demo_corpus.py",
        "note": (
            "Anchored to generation time. The fake warns when the corpus is "
            ">48h old; regenerate before a demo."
        ),
    }
    (out_dir / "demo_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"wrote {len(events)} events -> {events_path}")
    print("  by index:", ", ".join(f"{k}={v}" for k, v in sorted(by_index.items())))
    print(f"  window:   {events[0]['timestamp']} .. {events[-1]['timestamp']}")
    print(f"wrote notable  -> {notable_path}")
    print(f"wrote manifest -> {out_dir / 'demo_manifest.json'}")

    # Smoke: every playbook query must execute inside the SPL subset and return
    # rows for this corpus (the change-record card is expected to be empty).
    from services.rule_context_service import render_query_template
    from services.search_backend import MockSplunkBackend

    backend = MockSplunkBackend(seed_path=events_path)
    fields = {"host": CASE_HOST, "user": CASE_USER}
    print("playbook smoke (rendered -> rows):")
    failures = 0
    for query in PLAYBOOK_QUERIES:
        spl, unresolved = render_query_template(query["spl"], fields)
        try:
            rows = backend.search(spl, earliest="-7d")
            status = f"{len(rows)} rows"
        except ValueError as exc:
            status = f"ERROR {exc}"
            failures += 1
        print(f"  [{status}] {query['title']} :: {spl}")
        if unresolved:
            print(f"      unresolved placeholders: {unresolved}")
            failures += 1

    if args.db:
        seed_db(PLAYBOOK_QUERIES)

    print("next: python scripts/fake_splunk.py --seed %s" % events_path)
    print("      paste %s into the notable box, promote, then run the cards" % notable_path)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
