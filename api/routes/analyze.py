"""AI analysis endpoint: the investigation loop's engine.

``POST /api/db/analyze`` runs one loop iteration — prompt assembly, Ollama
generation, phase-2 query planning, and investigation-state derivation.
"""

import concurrent.futures
import json
import logging
import os
import sys

from fastapi import APIRouter, HTTPException

from api import deps
from api.helpers.errors import InternalError, raise_internal
from services.analysis_service import (
    build_analysis_prompt_intro,
    extract_phase2_evidence_json as _extract_phase2_evidence_json_impl,
    format_evidence_ledger_entries,
    normalize_phase2_text as _normalize_phase2_text,
    sanitize_analysis_text as _sanitize_analysis_text,
)
from services.investigation_state import (
    _apply_investigation_state_to_analysis_text,
    _build_investigation_state,
    _extract_analysis_sections,
    _extract_question_items,
    _serialize_investigation_state_record,
    _upsert_investigation_state,
)
from api.helpers.correlation import _normalize_rule_match_text, _resolve_correlation_rule
from api.helpers.phase2 import (
    PHASE_SPECIFIC_SUPPORTIVE_QUERIES,
    _already_run_supportive_titles,
    _annotate_phase2_targets,
    _build_question_driven_followup_queries,
    _build_supportive_phase2_fallback,
    _extract_phase2_queries,
    _ground_phase2_queries,
)
from api.schemas import AnalyzeRequest


def _utcnow():
    """Naive UTC now (platform convention)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


logger = logging.getLogger("soc.api")

# C2.1.1 hardening (DEVELOPMENT_PLAN §11, signed off Sept 30): wall-clock cap
# on the Ollama generation call. Over-limit runs answer 504 and persist
# nothing. Env-tunable so ops can adjust without a code edit.
ANALYZE_TIMEOUT_S = int(os.environ.get("ANALYZE_TIMEOUT_S", "300"))

router = APIRouter()


@router.post("/api/db/analyze", tags=["Database"])
def analyze_case(request: AnalyzeRequest):
    try:
        case_id = request.case_id
        context = request.context
        requested_analysis_stage = (request.analysis_stage or "initial").strip().lower() or "initial"
        # C3: per-stage model override; missing/empty entry falls back to
        # request.model ("" keeps the auto-resolve-an-installed-model behavior).
        model = (request.stage_models or {}).get(requested_analysis_stage) or request.model
        requested_phase_number = max(1, int(request.analysis_phase or 1))

        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQueryResult, ESCorrelationRule, SupportiveQuery, ClosureNote, InvestigationState, AnalysisResult

        db = SessionLocal()

        # 1. Anchor on the triage case
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        previous_state_record = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        previous_state_payload = _serialize_investigation_state_record(previous_state_record) if previous_state_record else {}

        # 2. Fetch Detection Science from ESCorrelationRule
        detection_rule = None
        if case.rule_id:
            detection_rule = db.query(ESCorrelationRule).filter(ESCorrelationRule.rule_id == case.rule_id).first()

        # If rule_id is missing or could not be resolved, fall back to a
        # relaxed rule_name match similar to the triage promotion logic so
        # Suspicious LotL and related rules still map to their canonical
        # ESCorrelationRule entries even when the case's rule_name includes
        # wrapper text like "Endpoint - <Rule Name> - Rule".
        if not detection_rule and case.rule_name:
            detection_rule = _resolve_correlation_rule(db, ESCorrelationRule, case.rule_name)
            if detection_rule and case.rule_id != detection_rule.rule_id:
                case.rule_id = detection_rule.rule_id
                db.commit()

        # Load pasted-notable events, baselines, and supportive queries
        pasted_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

        source_notable_payload = None
        for event in pasted_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue
            if payload.get("promoted_case_id") == case_id:
                source_notable_payload = {
                    "event_id": event.id,
                    "fields": payload.get("fields", {}),
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
                }
                break

        historical_baselines = []
        correlation_anchor = (case.rule_name or "").strip()
        if correlation_anchor:
            for event in pasted_events:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    continue
                if not payload.get("historical"):
                    continue
                fields = payload.get("fields", {})
                title = fields.get("title") or event.source or ""
                corr = fields.get("correlation_search") or ""
                if correlation_anchor in (title, corr):
                    historical_baselines.append({
                        "event_id": event.id,
                        "fields": fields,
                        "history": payload.get("history"),
                        "sanitized_text": payload.get("sanitized_text", ""),
                    })

        supportive_rows = db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id
        ).order_by(SupportiveQueryResult.created_at.desc()).all()

        supportive_results = []
        for r in supportive_rows:
            try:
                raw = json.loads(r.raw_result) if r.raw_result else None
            except Exception:
                raw = r.raw_result
            supportive_results.append({
                "id": r.id,
                "query_title": r.query_title,
                "source_system": r.source_system,
                "raw_result": raw,
            })

        # A Stage 3 rerun should test the original case and Phase 1 evidence
        # without making the saved Phase 2 answers part of the input. Keep all
        # rows for rebuilding the durable investigation state below.
        prompt_supportive_results = supportive_results
        if requested_analysis_stage == "initial":
            prompt_supportive_results = [
                item for item in supportive_results
                if (item.get("source_system") or "").strip().lower() != "phase2_manual"
            ]

        # Load prior structured closure notes for this rule to give the model
        # examples of how similar incidents have been closed historically.
        prior_closures = []
        if detection_rule:
            try:
                prior_rows = db.query(ClosureNote).filter(
                    ClosureNote.rule_id == detection_rule.rule_id
                ).order_by(ClosureNote.created_at.desc()).limit(5).all()
            except Exception:
                prior_rows = []

            for note in prior_rows:
                # Map internal status codes back to human-readable dispositions
                status_value = (note.status or "").strip().lower()
                status_to_disposition = {
                    "true_positive": "True Positive - Suspicious Activity",
                    "benign_positive": "Benign Positive - Suspicious But Expected",
                    "false_positive": "False Positive",
                    "other": "Other",
                    "undetermined": "Undetermined",
                }
                disposition_label = status_to_disposition.get(status_value, note.status or "")

                prior_closures.append({
                    "case_id": note.case_id,
                    "status": note.status,
                    "disposition": disposition_label,
                    "analyst_notes": note.analyst_notes or "",
                    "generated_note": note.generated_note or "",
                    "created_at": note.created_at.isoformat() if note.created_at else None,
                })

        # Load supportive SPL definitions tied to the rule so the model
        # can recommend additional queries to validate its hypothesis.
        # Imported Splunk notables may carry a custom UUID instead of the
        # platform rule_id, so resolve the query family from the notable
        # labels/fields before loading the playbook.
        supportive_query_defs = []
        supportive_rule_id = (detection_rule.rule_id if detection_rule else (case.rule_id or "")).strip()
        catalog_supportive_path = os.path.join(deps.get_platform_root(), "supportive_rules.json")
        catalog_rules = []
        if os.path.isfile(catalog_supportive_path):
            try:
                with open(catalog_supportive_path, "r", encoding="utf-8") as rf:
                    catalog_rules = json.load(rf).get("rules") or []
            except Exception as ex:
                logger.warning("Failed to load supportive rule catalog: %s", ex)

        if catalog_rules:
            notable_fields = (source_notable_payload or {}).get("fields") or {}
            anchor_text = " ".join(
                str(value) for value in [
                    case.rule_name,
                    notable_fields.get("correlation_search"),
                    notable_fields.get("title"),
                    notable_fields.get("description"),
                    notable_fields.get("file_path"),
                    notable_fields.get("file_name"),
                    notable_fields.get("process"),
                    notable_fields.get("parent_process"),
                ] if value
            )
            normalized_anchor = _normalize_rule_match_text(anchor_text)
            # A rule that owns DB-backed supportive queries is the authoritative
            # family for this case: text similarity to an unrelated catalog
            # entry must never hijack its rule_id (seeded/custom playbooks
            # otherwise lost their cards mid-investigation, leaving Stage 4
            # with no queries and no generator).
            case_has_db_queries = bool(
                supportive_rule_id
                and db.query(SupportiveQuery.id)
                .filter(SupportiveQuery.rule_id == supportive_rule_id)
                .first()
            )
            exact_catalog = next(
                (item for item in catalog_rules
                 if (item.get("rule_id") or "").strip() == supportive_rule_id),
                None,
            )
            if not exact_catalog and not case_has_db_queries:
                anchor_tokens = set(normalized_anchor.split())
                best_catalog = None
                best_score = 0
                for item in catalog_rules:
                    candidate_text = " ".join([
                        str(item.get("rule_id") or ""),
                        str(item.get("rule_name") or ""),
                        " ".join(str(q.get("title") or "") for q in (item.get("supportive_queries") or [])),
                    ])
                    candidate_tokens = set(_normalize_rule_match_text(candidate_text).split())
                    score = len(anchor_tokens & candidate_tokens)
                    if score > best_score:
                        best_catalog = item
                        best_score = score
                if best_catalog and best_score >= 2:
                    supportive_rule_id = (best_catalog.get("rule_id") or "").strip()

        if supportive_rule_id and case.rule_id != supportive_rule_id:
            case.rule_id = supportive_rule_id
            # Persist the normalized family ID without expiring the ORM
            # object before the prompt-building code finishes using it.
            db.flush()

        if supportive_rule_id:
            rule_id = supportive_rule_id

            # Base queries explicitly keyed to this rule_id
            supportive_query_defs.extend(
                db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_id).all()
            )

            # LotL family sharing: reuse canonical queries for related "lotl_*" rules
            canonical_lotl_id = "lotl_outbound_connection"
            if rule_id.startswith("lotl_") and rule_id != canonical_lotl_id:
                extras = db.query(SupportiveQuery).filter(
                    SupportiveQuery.rule_id == canonical_lotl_id
                ).all()
                existing_titles = {q.title for q in supportive_query_defs}
                for q in extras:
                    if q.title not in existing_titles:
                        supportive_query_defs.append(q)

            # Merge specialized follow-up queries from the catalog already
            # loaded above. This also works when the DB has no matching
            # SupportiveQuery rows but the checked-in playbook is present.
            try:
                existing_titles = {_normalize_phase2_text(getattr(q, "title", "")) for q in supportive_query_defs}
                for r_entry in catalog_rules:
                    if (r_entry.get("rule_id") or "").strip().lower() == rule_id.lower():
                        for sq in (r_entry.get("supportive_queries") or []):
                            t = sq.get("title") or ""
                            if _normalize_phase2_text(t) not in existing_titles:
                                class VirtualQuery:
                                    def __init__(self, t, d, s, qid=None):
                                        self.id = qid
                                        self.title = t
                                        self.description = d
                                        self.spl_query = s
                                phase_min = int(sq.get("phase_min") or 2)
                                if phase_min <= requested_phase_number:
                                    supportive_query_defs.append(VirtualQuery(t, sq.get("description") or "", sq.get("spl_query") or "", sq.get("id")))
                                    existing_titles.add(_normalize_phase2_text(t))
            except Exception as ex:
                logger.warning("Failed to merge supportive rule catalog: %s", ex)

            if requested_phase_number > 2:
                existing_titles = {
                    _normalize_phase2_text(
                        query_def.get("title") if isinstance(query_def, dict) else getattr(query_def, "title", "")
                    )
                    for query_def in supportive_query_defs
                }
                for query_def in PHASE_SPECIFIC_SUPPORTIVE_QUERIES.get(rule_id, []):
                    title_key = _normalize_phase2_text(query_def.get("title"))
                    if title_key not in existing_titles:
                        supportive_query_defs.append(query_def)
                        existing_titles.add(title_key)

        db.close()

        prior_analysis = (request.prior_analysis or "").strip()
        analysis_stage = requested_analysis_stage
        prior_analysis_marker = "PHASE2_QUERIES_JSON_START"
        prior_analysis_marker_idx = prior_analysis.find(prior_analysis_marker)
        if prior_analysis_marker_idx != -1:
            prior_analysis = prior_analysis[:prior_analysis_marker_idx].strip()
        if len(prior_analysis) > 3000:
            prior_analysis = prior_analysis[:3000].strip()

        client = deps.get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")

        # 3. Ask Ollama only for the reasoning that benefits from an LLM.
        # Verdict/confidence, grounded Phase 2 cards, and closure gating are
        # calculated by deterministic platform logic after this call.
        prompt_intro = build_analysis_prompt_intro(has_prior_analysis=bool(prior_analysis))

        prompt_parts = [
            "You are an expert SOC Analyst triaging a security incident.",
            prompt_intro,
        ]

        if detection_rule:
            prompt_parts.append("\n\n=== DETECTION SCIENCE & CORRELATION LOGIC ===")
            prompt_parts.append(f"Rule ID: {detection_rule.rule_id}")
            prompt_parts.append(f"Rule Name: {detection_rule.rule_name}")
            prompt_parts.append(f"Description / Hypothesis: {detection_rule.description}")
            prompt_parts.append(f"Category / Domain: {detection_rule.category}")
            prompt_parts.append(f"Severity: {detection_rule.severity}")
            if detection_rule.drilldown_fields:
                prompt_parts.append(f"Key Drilldown Fields: {detection_rule.drilldown_fields}")

        prompt_parts.append("\n\n=== CURRENT CASE ===")
        prompt_parts.append(f"Case ID: {case.case_id} | Rule: {case.rule_name} | Initial Verdict: {case.verdict}")
        prompt_parts.append(f"Summary: {case.analysis_summary}")

        if previous_state_payload:
            prompt_parts.append("\n\n=== INVESTIGATION LOOP STATE ===")
            prompt_parts.append(f"Loop Status: {previous_state_payload.get('loop_status')}")
            prompt_parts.append(f"Iteration Count: {previous_state_payload.get('iteration_count')}")
            prompt_parts.append(f"Current Hypothesis: {previous_state_payload.get('current_hypothesis')}")
            prompt_parts.append(f"Provisional Disposition: {previous_state_payload.get('provisional_disposition')}")
            prompt_parts.append(f"Disposition Confidence: {previous_state_payload.get('disposition_confidence')}")
            unresolved = previous_state_payload.get("unresolved_questions") or []
            if unresolved:
                prompt_parts.append("Open Questions:")
                for item in unresolved[:5]:
                    prompt_parts.append(f"- {item}")
            blockers = previous_state_payload.get("closure_blockers") or []
            if blockers:
                prompt_parts.append("Closure Blockers:")
                for item in blockers[:5]:
                    prompt_parts.append(f"- {item}")
            if unresolved or blockers:
                prompt_parts.append("Prioritize Phase 2 checks that directly resolve these open questions and closure blockers. Every follow-up query should have a clear disposition-changing purpose.")

        if prior_analysis:
            prompt_parts.append("\n\n=== PREVIOUS ANALYSIS HYPOTHESIS ===")
            prompt_parts.append(f"Analysis Stage: {analysis_stage}")
            prompt_parts.append(prior_analysis)

        if source_notable_payload:
            prompt_parts.append("\n\n=== SOURCE NOTABLE EVIDENCE ===")
            if source_notable_payload.get("fields"):
                for k, v in list(source_notable_payload["fields"].items())[:25]:
                    prompt_parts.append(f"- {k}: {v}")
            if source_notable_payload.get("sanitized_text"):
                sanitized_text = str(source_notable_payload["sanitized_text"])[:2500]
                prompt_parts.append(f"\nRaw Sanitized Notable:\n{sanitized_text}")

        if historical_baselines:
            prompt_parts.append("\n\n=== HISTORICAL CLOSURE BASELINES ===")
            prompt_parts.append("Use these prior closed notables only as contextual examples; do not treat them as proof of the current case.")
            for baseline in historical_baselines[:5]:
                fields = baseline.get("fields") or {}
                prompt_parts.append(
                    f"- Event {baseline.get('event_id')}: title={fields.get('title') or fields.get('correlation_search') or 'unknown'}; "
                    f"host={fields.get('host') or fields.get('destination') or 'unknown'}; "
                    f"user={fields.get('user') or fields.get('username') or 'unknown'}; "
                    f"process={fields.get('process') or fields.get('process_name') or 'unknown'}; "
                    f"disposition={fields.get('disposition') or 'unknown'}; "
                    f"closure_summary={str(baseline.get('history') or '')[:500]}"
                )

        if prior_closures:
            prompt_parts.append("\n\n=== PRIOR STRUCTURED CLOSURE NOTES ===")
            prompt_parts.append("Use prior notes as disposition context, not as a substitute for current evidence.")
            for prior in prior_closures[:5]:
                prompt_parts.append(
                    f"- {prior.get('case_id')}: disposition={prior.get('disposition') or prior.get('status')}; "
                    f"note={str(prior.get('generated_note') or '')[:700]}"
                )

        if prompt_supportive_results:
            prompt_parts.append("\n\n=== INVESTIGATION EVIDENCE ===")
            prompt_parts.extend(format_evidence_ledger_entries(prompt_supportive_results))

        if context:
            prompt_parts.append(f"\n\n=== ANALYST CONTEXT ===\n{context[:1000]}")

        composite_prompt = "\n".join(prompt_parts)
        # C2.1.1: run the model call under a wall-clock cap. The 504 must not
        # wait for the hung request, so the executor is abandoned with
        # shutdown(wait=False) instead of a `with` block — the worker thread
        # finishes (or dies with the process) on its own. The timeout raises
        # BEFORE any DB write below, so nothing is persisted.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            client.generate,
            composite_prompt,
            model=model,
            temperature=0.1,
            # Section 4 (Per-Evidence Assessment) adds a per-entry line each;
            # 320 tokens truncated it mid-sentence, losing later verdicts.
            # The service default (OLLAMA_NUM_PREDICT env) is 500, so the
            # per-card flow explicitly needs the larger budget.
            options={"num_predict": int(os.environ.get("OLLAMA_ANALYSIS_NUM_PREDICT", "640"))},
        )
        try:
            result = future.result(timeout=ANALYZE_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False)
            raise HTTPException(
                status_code=504,
                detail=f"Analysis timed out after {ANALYZE_TIMEOUT_S}s — nothing was persisted",
            )
        executor.shutdown(wait=False)
        # Report the tag actually used after auto-resolution so API consumers
        # and the audit trail reflect reality, not the (possibly empty)
        # requested value.
        model = result.get("model") or model

        if not result["success"]:
            raise InternalError(result["error"], context="analyze")

        response_text = result["response"] or ""
        phase2_queries = _extract_phase2_queries(response_text)
        # Phase 2: structured per-card verdicts (title -> direction/rationale).
        # Empty list = block absent/malformed; everything downstream falls back
        # to the Per-Evidence Assessment text parser + global heuristic.
        phase2_evidence_verdicts = _extract_phase2_evidence_json_impl(response_text)


        already_run_titles = _already_run_supportive_titles(prompt_supportive_results)
        phase_query_defs = supportive_query_defs
        if requested_phase_number > 2:
            phase_query_defs = [
                query_def for query_def in supportive_query_defs
                if _normalize_phase2_text(
                    query_def.get("title") if isinstance(query_def, dict) else getattr(query_def, "title", "")
                ) not in already_run_titles
            ]
        blocker_context = " ".join(
            [str(item) for item in (previous_state_payload.get("unresolved_questions") or [])]
            + [str(item) for item in (previous_state_payload.get("closure_blockers") or [])]
        )

        # Later follow-up phases must advance the investigation rather than
        # replaying model-generated titles from an earlier phase. The model
        # still contributes reasoning, but the visible cards come only from
        # unused, grounded playbook definitions for this phase.
        if requested_phase_number > 2:
            # Pass the FULL template pool: when every template has already
            # been run, the phase-filtered list is empty and the variant
            # builder would have nothing to derive question-targeted re-check
            # cards from.
            phase2_queries = _build_question_driven_followup_queries(
                supportive_query_defs,
                previous_state_payload or {},
                prompt_supportive_results,
                requested_phase_number,
                current_questions=_extract_question_items(
                    _extract_analysis_sections(response_text).get("key questions", "")
                ),
            )
            if not phase2_queries:
                phase2_queries = _build_supportive_phase2_fallback(
                    phase_query_defs,
                    f"{prior_analysis} {blocker_context}",
                    response_text,
                    already_run_titles=already_run_titles,
                )
            # NOTE: do NOT run _ground_phase2_queries here. The fallback and
            # question-driven variant builders already emit only playbook-
            # grounded payloads, and grounding would discard the phase-
            # annotated variant titles (they intentionally differ from the
            # template titles), reintroducing the empty Phase 3+ card list.
        elif not phase2_queries:
            phase2_queries = _build_supportive_phase2_fallback(
                phase_query_defs,
                f"{prior_analysis} {blocker_context}",
                response_text,
                already_run_titles=already_run_titles,
            )

        if requested_phase_number <= 2:
            phase2_queries = _ground_phase2_queries(
                phase2_queries,
                phase_query_defs,
                already_run_titles=already_run_titles,
            )
        phase2_queries = _annotate_phase2_targets(phase2_queries, previous_state_payload)
        display_analysis = _sanitize_analysis_text(response_text, phase2_queries)
        investigation_state = _build_investigation_state(
            case,
            display_analysis,
            phase2_queries,
            supportive_results,
            analysis_stage,
            previous_state_payload,
            evidence_verdicts=phase2_evidence_verdicts,
        )
        display_analysis = _apply_investigation_state_to_analysis_text(display_analysis, investigation_state)
        # Persist every completed AI iteration. The browser response is not
        # the system of record: saved prompts/responses let later iterations,
        # reviewers, and closure generation audit exactly what the local model
        # saw and produced.
        db.add(AnalysisResult(
            case_id=case_id,
            model_name=model,
            query=composite_prompt,
            analysis=display_analysis,
            confidence=float(investigation_state.get("disposition_confidence") or 0.0),
        ))
        _upsert_investigation_state(db, InvestigationState, investigation_state)

        # Persist the model's per-card verdicts onto the evidence rows so the
        # ledger itself carries the AI judgment (survives later reloads and is
        # shown in the UI without re-running analysis). NOTE: db was closed()
        # before the model call, so supportive_rows are detached ORM objects —
        # mutating them would be silently lost. Re-query the rows fresh so
        # they belong to the live session.
        timeline_by_id = {
            entry.get("id"): entry
            for entry in (investigation_state.get("evidence_summary") or {}).get("timeline") or []
            if entry.get("id") is not None
        }
        verdict_rows_updated = 0
        if timeline_by_id:
            live_rows = db.query(SupportiveQueryResult).filter(
                SupportiveQueryResult.case_id == case_id
            ).all()
            for row in live_rows:
                entry = timeline_by_id.get(row.id)
                if not entry or entry.get("ai_verdict_source") not in ("per_card", "evidence_json"):
                    continue
                try:
                    raw_obj = json.loads(row.raw_result) if row.raw_result else {}
                except Exception:
                    raw_obj = {"result_text": row.raw_result} if row.raw_result else {}
                if not isinstance(raw_obj, dict):
                    continue
                if raw_obj.get("ai_finding_type") == entry.get("finding_type") and raw_obj.get("ai_verdict_rationale") == entry.get("ai_verdict_rationale"):
                    continue
                raw_obj["ai_finding_type"] = entry.get("finding_type")
                raw_obj["ai_verdict_rationale"] = entry.get("ai_verdict_rationale") or ""
                raw_obj["ai_verdict_source"] = entry.get("ai_verdict_source")
                raw_obj["ai_assessed_at"] = _utcnow().isoformat()
                row.raw_result = json.dumps(raw_obj, ensure_ascii=False)
                verdict_rows_updated += 1
        # Commit unconditionally: the AnalysisResult and InvestigationState
        # writes above must persist even when no per-card verdicts were
        # applied (e.g. empty ledger).
        db.commit()

        return {
            "case_id": case_id,
            "model": model,
            "analysis": display_analysis,
            # Keep the summary fields available to the UI and API consumers;
            # the authoritative values are calculated in investigation_state.
            "verdict": investigation_state.get("provisional_disposition") or "undetermined",
            "confidence": float(investigation_state.get("disposition_confidence") or 0.0),
            "analysis_sections": _extract_analysis_sections(response_text),
            "analysis_stage": analysis_stage,
            "used_prior_analysis": bool(prior_analysis),
            "detection_science_applied": bool(detection_rule),
            "baseline_notables_count": len(historical_baselines),
            "supportive_results_count": len(supportive_results),
            "per_card_verdicts_applied": int((investigation_state.get("evidence_summary") or {}).get("per_card_verdicts_applied") or 0),
            "investigation_evidence_count": len(supportive_results),
            "supportive_playbook_available": bool(supportive_query_defs),
            "supportive_rule_id": supportive_rule_id or (case.rule_id or ""),
            "ollama_metrics": {
                "eval_tokens": result.get("tokens", 0),
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "total_duration_seconds": round((result.get("total_duration_ns", 0) or 0) / 1_000_000_000, 1),
                "load_duration_seconds": round((result.get("load_duration_ns", 0) or 0) / 1_000_000_000, 1),
            },
            "supportive_queries": [
                {
                    "id": q.get("id") if isinstance(q, dict) else getattr(q, "id", None),
                    "title": q.get("title") if isinstance(q, dict) else q.title,
                    "description": q.get("description") if isinstance(q, dict) else q.description,
                    "spl_query": q.get("spl_query") if isinstance(q, dict) else q.spl_query,
                }
                for q in supportive_query_defs
            ],
            "prior_closures": prior_closures,
            "phase2_queries": phase2_queries,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise_internal(e, context="analyze")


