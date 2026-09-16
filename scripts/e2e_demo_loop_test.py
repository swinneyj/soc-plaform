#!/usr/bin/env python3
"""End-to-end rehearsal of the interactive investigation loop (demo Monday).

Runs against the live local API without touching existing cases:
  paste notable -> promote -> initial analysis -> save Phase 2 evidence
  -> follow-up analysis -> closure-readiness -> closure note -> cleanup
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
MODEL = "llama3.1:latest"
MARKER = "E2E-DEMO-LOOP"


def call(method, path, payload=None, timeout=240):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def step(msg):
    print(f"\n=== {msg}", flush=True)


def main():
    results = {"pass": [], "fail": []}

    def check(name, ok, detail=""):
        results["pass" if ok else "fail"].append(f"{name}: {detail}")
        print(("  [PASS] " if ok else "  [FAIL] ") + f"{name} {detail}", flush=True)

    # 1. Paste a synthetic notable
    step("1. Paste synthetic notable")
    raw_text = "\n".join(
        [
            f"title: {MARKER} Encoded PowerShell Execution",
            "correlation_search: PowerShell In-Memory Execution",
            "description: Simulated in-memory PowerShell execution with encoded payload by admin user on test host during exercise window.",
            "destination: e2e-demo-host",
            "user: demo.admin",
            "process: powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA",
            "parent_process: svchost.exe -k netsvcs",
            "severity: high",
            "urgency: high",
            "status: New",
            "owner: unassigned",
            "security_domain: endpoint",
            "time: 2026-09-11T19:30:00",
            "count: 1",
            "type: notable",
        ]
    )
    paste = call("POST", "/api/db/notables/paste", {"raw_text": raw_text})
    print("  paste response keys:", sorted(paste.keys()) if isinstance(paste, dict) else type(paste))

    # 2. Find the new notable id
    notables = call("GET", "/api/db/notables?limit=50")
    mine = [
        n for n in notables
        if MARKER in (n.get("title") or "") and not n.get("historical")
    ]
    if not mine:
        check("paste+locate", False, "notable not found after paste")
        print(json.dumps(results, indent=2))
        sys.exit(1)
    mine.sort(key=lambda n: n["id"])
    event = mine[-1]
    check("paste+locate", True, f"event_id={event['id']}")

    # 3. Promote to triage case
    step("2. Promote to triage case")
    promo = call("POST", f"/api/db/notables/{event['id']}/promote", timeout=60)
    case_id = promo.get("case_id")
    check("promote", bool(case_id), f"case_id={case_id} verdict={promo.get('verdict')}")

    # 4. Initial analysis (Ollama)
    step("3. Initial AI assessment (llama3.1) — this is the slow step")
    t0 = time.time()
    analysis = call(
        "POST", "/api/db/analyze",
        {"case_id": case_id, "model": MODEL, "analysis_stage": "initial"},
        timeout=300,
    )
    elapsed = time.time() - t0
    text = analysis.get("analysis") or analysis.get("result") or ""
    pq = analysis.get("phase2_queries") or analysis.get("phase_2_queries") or []
    check("initial_analysis", bool(text), f"{elapsed:.0f}s, {len(text)} chars")
    check("phase2_queries_generated", len(pq) > 0, f"{len(pq)} queries")
    for q in pq[:4]:
        print("   -", (q.get("title") or "?")[:70])
    state0 = analysis.get("investigation_state") or {}
    qs0 = (state0.get("evidence_summary") or {}).get("unresolved_questions") or state0.get("unresolved_questions") or []
    print("  open questions after initial:", len(qs0))
    for q in qs0[:5]:
        print("   ?", q[:80])

    # Pick target questions for evidence
    targets = qs0[:3] if qs0 else []

    # 5. Save Phase 2 evidence resolving the questions
    step("4. Save Phase 2 evidence (2 supporting + 1 refuting, resolving inquiries)")
    entries = [
        {
            "query_title": "Encoded Payload Decoding Result",
            "query_text": "index=proxy src=e2e-demo-host | head 10",
            "result_text": (
                "Decoded payload runs an in-memory PowerShell command that uses "
                "Net.WebClient.DownloadString against https://updates.corp.example/payload.ps1 "
                "and executes returned content with IEX. Confirms the purpose of the encoded payload."
            ),
            "analyst_summary": "Decoded payload confirmed: remote download-and-execute.",
            "finding_type": "supports",
            "question_resolution": "resolved",
            "target_questions": targets[:1],
            "result_status": "success",
            "source_system": "phase2_manual",
        },
        {
            "query_title": "Recent PowerShell Activity - Host Sweep",
            "query_text": "index=edr host=e2e-demo-host EventCode=4688 | stats count by process",
            "result_text": (
                "Only benign Get-MpComputerStatus and one encoded execution found. "
                "No additional suspicious PowerShell executions on the host."
            ),
            "analyst_summary": "Host sweep clean beyond the alert itself.",
            "finding_type": "refutes",
            "question_resolution": "resolved",
            "target_questions": targets[1:2],
            "result_status": "success",
            "source_system": "phase2_manual",
        },
        {
            "query_title": "Script Hash Baseline Check",
            "query_text": "index=edr host=e2e-demo-host | stats dc(script_hash) as hashes",
            "result_text": (
                "1 distinct script hash. Hash not found in authorized script baseline or known-good catalog. "
                "No maintenance ticket or approved software deployment identified."
            ),
            "analyst_summary": "Unknown script, unsupported by change control.",
            "finding_type": "supports",
            "question_resolution": "resolved",
            "target_questions": targets[2:3],
            "result_status": "success",
            "source_system": "phase2_manual",
        },
    ]
    ev = call(
        "POST", f"/api/db/triage/{case_id}/evidence",
        {"entries": entries, "replace_existing": True, "source_system": "phase2_manual"},
        timeout=60,
    )
    check("evidence_saved", ev.get("saved_count", 0) >= 3 or ev.get("success"), json.dumps({k: ev.get(k) for k in ("saved_count", "success", "skipped_count") if k in ev}))

    # 6. Follow-up analysis (evidence in, phase2 excluded from prompt like the UI)
    step("5. Follow-up analysis (AI re-scores with evidence)")
    t0 = time.time()
    follow = call(
        "POST", "/api/db/analyze",
        {
            "case_id": case_id, "model": MODEL,
            "analysis_stage": "follow_up",
            "prior_analysis": text[:3000],
        },
        timeout=300,
    )
    elapsed = time.time() - t0
    state = follow.get("investigation_state") or {}
    summary = state.get("evidence_summary") or {}
    conf = state.get("confidence")
    disposition = state.get("provisional_disposition") or state.get("disposition")
    blockers = summary.get("active_blockers") or state.get("active_blockers") or []
    unresolved = summary.get("unresolved_questions") or state.get("unresolved_questions") or []
    resolved = summary.get("resolved_questions") or state.get("resolved_questions") or []
    check("follow_up_analysis", True, f"{elapsed:.0f}s")
    print(f"  disposition={disposition} confidence={conf}")
    print(f"  resolved={len(resolved)} unresolved={len(unresolved)} blockers={len(blockers)}")
    for r in resolved:
        print("   resolved:", r[:80])
    check("resolution_durable", len(resolved) >= 2, f"{len(resolved)} resolved questions")
    check("no_unresolved_blockers", len(unresolved) == 0 and len(blockers) == 0, f"unresolved={len(unresolved)} blockers={len(blockers)}")
    rfc = state.get("ready_for_closure")
    check("ready_for_closure_flag", rfc is True, f"ready_for_closure={rfc}")

    # 7. Closure readiness endpoint
    step("6. Closure readiness check")
    readiness = call("GET", f"/api/db/triage/{case_id}/closure-readiness", timeout=30)
    print("  readiness:", json.dumps(readiness)[:400])
    ready = readiness.get("ready_for_closure", readiness.get("eligible", False)) if isinstance(readiness, dict) else False
    check("closure_gate_reachable", bool(ready), json.dumps(readiness)[:200] if isinstance(readiness, dict) else "?")

    # 8. Closure note
    step("7. Generate closure note")
    note = call(
        "POST", "/api/db/closure-note",
        {"case_id": case_id, "disposition": "Malicious", "analyst_notes": "Demo rehearsal closure."},
        timeout=120,
    )
    note_ok = bool((note.get("note") or note.get("generated_note") or "")) if isinstance(note, dict) else bool(note)
    check("closure_note_generated", note_ok, str({k: str(v)[:60] for k, v in note.items()})[:200] if isinstance(note, dict) else "?")

    # 9. Cleanup: remove the demo case and notable
    step("8. Cleanup demo case + notable")
    try:
        call("POST", f"/api/db/triage/{case_id}/delete", timeout=30)
        check("cleanup_case", True, case_id)
    except Exception as ex:
        check("cleanup_case", False, str(ex)[:120])
    try:
        call("POST", f"/api/db/notables/{event['id']}/delete", timeout=30)
        check("cleanup_notable", True, f"event_id={event['id']}")
    except Exception as ex:
        check("cleanup_notable", False, str(ex)[:120])

    # Verdict
    step("RESULT")
    print(f"PASSED {len(results['pass'])} / {len(results['pass']) + len(results['fail'])}")
    for f in results["fail"]:
        print("  FAILED:", f)
    sys.exit(0 if not results["fail"] else 2)


if __name__ == "__main__":
    main()
