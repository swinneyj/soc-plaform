"""Phase-2 / supportive follow-up query building and grounding.

Thin wrappers over the canonical services.analysis_service implementations
plus the question-driven follow-up builder that assembles Phase 3+ cards
from unresolved inquiries."""
import re
from typing import Any, Dict, List, Optional

from services.analysis_service import (
    extract_phase2_queries as _extract_phase2_queries_impl,
    ground_phase2_queries as _ground_phase2_queries_impl,
    normalize_phase2_text as _normalize_phase2_text,
)

PHASE_SPECIFIC_SUPPORTIVE_QUERIES = {
    "linux_ssh_key_creation": [
        {
            "title": "Authorized key ownership and session context",
            "description": "Determine which account, audit session, source address, and command context were associated with the authorized_keys change.",
            "spl_query": "index=nix host=\"$host$\" (\"authorized_keys\" OR \"ssh-keygen\") | rex field=_raw \"name=\\\"(?<file_path>[^\\\"]+)\\\"\" | rex field=_raw \"comm=\\\"(?<command>[^\\\"]+)\\\"\" | rex field=_raw \"acct=\\\"(?<account>[^\\\"]+)\\\"\" | rex field=_raw \"auid=(?<auid>\\d+)\" | rex field=_raw \"ses=(?<session_id>\\d+)\" | rex field=_raw \"addr=(?<src_ip>\\S+)\" | search file_path=\"*/.ssh/authorized_keys*\" OR command=\"ssh-keygen\" | stats earliest(_time) as first_seen latest(_time) as last_seen values(account) as accounts values(auid) as auids values(session_id) as sessions values(src_ip) as source_ips values(command) as commands by host file_path | sort 0 -last_seen"
        },
        {
            "title": "Other suspicious activity on host",
            "description": "Look for additional persistence, privilege, download, or network activity on the affected host around the key modification.",
            "spl_query": "index=nix host=\"$host$\" earliest=-24h (\"authorized_keys\" OR \"ssh-keygen\" OR \"sudo\" OR \"curl\" OR \"wget\" OR \"nc\" OR \"chmod\" OR \"chown\") | rex field=_raw \"comm=\\\"(?<command>[^\\\"]+)\\\"\" | rex field=_raw \"exe=\\\"(?<exe>[^\\\"]+)\\\"\" | rex field=_raw \"name=\\\"(?<file_path>[^\\\"]+)\\\"\" | stats count as events earliest(_time) as first_seen latest(_time) as last_seen values(command) as commands values(exe) as executables values(file_path) as file_paths by host | sort -events"
        }
    ]
}# The extract/ground/looks-like-SPL logic lives ONLY in the services layer now
# (services/analysis_service.py) — this module keeps thin aliases for its many
# internal call sites and for historical importers. Do not reintroduce inline
# copies: the earlier twins had drifted (marker-tag-only extraction, different
# default descriptions, different unused-first ordering). Extend the service
# copy instead.


def _extract_phase2_queries(response_text: str) -> List[Dict[str, Any]]:
    return _extract_phase2_queries_impl(response_text)



def _already_run_supportive_titles(supportive_results=None) -> set:
    """Titles that already have saved investigation evidence for this case."""
    titles = set()
    for item in supportive_results or []:
        title = (item.get("query_title") or "").strip()
        if not title:
            continue
        raw = item.get("raw_result")
        has_result = False
        if isinstance(raw, dict):
            has_result = bool(
                (raw.get("result_text") or "").strip()
                or (raw.get("analyst_summary") or "").strip()
            )
        elif raw:
            has_result = True
        if has_result:
            titles.add(_normalize_phase2_text(title))
    return titles



def _ground_phase2_queries(
    phase2_queries: List[Dict[str, Any]],
    supportive_query_defs,
    already_run_titles=None,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    """Map model Phase 2 suggestions onto real supportive playbook templates only.

    Thin alias for services.analysis_service.ground_phase2_queries — the
    canonical implementation (dict-or-object template support, containment
    matching, unused-first ordering, ranked fallback) lives there. See the
    note above _extract_phase2_queries.
    """
    return _ground_phase2_queries_impl(
        phase2_queries,
        supportive_query_defs,
        already_run_titles=already_run_titles,
        max_queries=max_queries,
    )


# NOTE: the investigation-state formatting helpers (_format_state_label,
# _format_state_verdict_section, _format_state_closure_section) and the
# grounded Phase 2 section formatter live ONLY in the services layer now:
#   - services/investigation_state.py (state label/verdict/closure sections)
#   - services/analysis_service.py (format_grounded_phase2_section, used by
#     sanitize_analysis_text)
# Earlier copies here had drifted (wording, bold markers) and are deleted.
# Do not reintroduce inline copies — import from the service or extend it.



def _rescope_variant_spl(spl: str, phase_number: int) -> str:
    """Give a re-check variant a genuinely distinct, phase-scoped SPL.

    Re-running an identical query is not a new query: variants re-scope the
    search-time window (``earliest=-<phase>h``) so each iteration can surface
    fresh rows instead of replaying a saved card's SPL under a new title.
    """
    text = (spl or "").strip()
    if not text:
        return text
    window = f"earliest=-{max(1, int(phase_number))}h"
    if re.search(r"\bearliest=\S+", text):
        return re.sub(r"\bearliest=\S+", window, text, count=1)
    pipe_idx = text.find("|")
    if pipe_idx == -1:
        return f"{text} {window}"
    return f"{text[:pipe_idx]}{window} {text[pipe_idx:]}"


def _build_question_driven_followup_queries(
    supportive_query_defs,
    previous_state_payload: Dict[str, Any],
    prompt_supportive_results: List[Dict[str, Any]],
    phase_number: int,
    max_queries: int = 3,
    current_questions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build follow-up cards for later phases that target the CURRENT open
    questions, even after every playbook template has already been run.

    Strategy, in order:
    1. Unused playbook templates (grounded, never run) — preferred.
    2. Fresh variants of already-run templates: clone the closest-matching
       template for each open question/blocker, annotate it with the phase
       number and the target it addresses, and mark it as a variant so the
       analyst understands it is a re-scoped run (phase-specific time
       window), not a replay.
    3. No explicit targets but the loop has not converged
       (loop_status != ready_for_closure): hypothesis-verification re-checks,
       so a follow-up phase never dead-ends while the case still needs work.

    This guarantees the UI never shows an empty/stale query list — the
    Phase 3+ dead end ("SPL phase degradation", DEVELOPMENT_PLAN §8).
    """
    if phase_number <= 2:
        return []

    already_run_titles = _already_run_supportive_titles(prompt_supportive_results)

    # 1) Prefer genuinely unused playbook templates.
    unused = _build_supportive_phase2_fallback(
        supportive_query_defs,
        "",
        "",
        already_run_titles=already_run_titles,
        max_queries=max_queries,
    )
    unused = [q for q in unused if _normalize_phase2_text(q.get("title")) not in already_run_titles]
    if unused:
        return unused

    # 2) All templates used — build question-targeted variants of the
    #    closest-matching already-run templates.
    questions = [str(q).strip() for q in (previous_state_payload.get("unresolved_questions") or []) if str(q).strip()]
    # Questions this analysis just raised are open work NOW — merge them in so
    # the loop reacts immediately instead of one iteration later.
    for question in (current_questions or []):
        text = str(question).strip()
        if text and text not in questions:
            questions.append(text)
    blockers = [
        str(b).strip()
        for b in (previous_state_payload.get("closure_blockers") or [])
        if str(b).strip() and "remain unresolved" not in str(b).lower()
    ]
    targets = questions + blockers

    # 3) No explicit targets but the loop has not converged: keep it alive
    #    with hypothesis-verification re-checks. A follow-up phase must never
    #    dead-end while the case still needs work (the phase-degradation bug).
    if not targets:
        loop_status = (previous_state_payload.get("loop_status") or "").strip().lower()
        if loop_status == "ready_for_closure":
            return []
        hypothesis = (previous_state_payload.get("current_hypothesis") or "").strip()
        targets = [hypothesis or "Verify the disposition-driving evidence before concluding the investigation"]

    defs = []
    for query_def in supportive_query_defs or []:
        if isinstance(query_def, dict):
            title = (query_def.get("title") or "").strip()
            spl_query = (query_def.get("spl_query") or query_def.get("spl") or "").strip()
            description = (query_def.get("description") or "").strip()
        else:
            title = (getattr(query_def, "title", "") or "").strip()
            spl_query = (getattr(query_def, "spl_query", "") or "").strip()
            description = (getattr(query_def, "description", "")).strip() if getattr(query_def, "description", None) else ""
        if title and spl_query:
            defs.append({"title": title, "spl": spl_query, "description": description})
    if not defs:
        return []

    variants = []
    used_in_batch: set = set()
    for target in targets[:max_queries]:
        target_words = set(re.findall(r"[a-z0-9]+", target.lower()))
        ranked = []
        for candidate in defs:
            candidate_text = " ".join([
                candidate["title"], candidate["description"], candidate["spl"],
            ]).lower()
            overlap = len(target_words & set(re.findall(r"[a-z0-9]+", candidate_text)))
            ranked.append((overlap, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        # Prefer a template this batch has not already re-checked (variety);
        # fall back to the best match so every target still gets a card.
        best = next((c for _o, c in ranked if c["title"] not in used_in_batch), None)
        if best is None and ranked:
            best = ranked[0][1]
        if not best:
            continue
        used_in_batch.add(best["title"])
        short_question = target if len(target) <= 90 else target[:87].rstrip() + "..."
        variants.append({
            "title": f"Phase {phase_number}: {best['title']} (targeted re-check)",
            "spl": _rescope_variant_spl(best["spl"], phase_number),
            "description": (
                f"Re-scoped Phase {phase_number} run of '{best['title']}' targeting: "
                f"\"{short_question}\". Refine the time window or add context before running; the AI will "
                "assess the new result against this target."
            ),
            "target_questions": [target],
            "is_variant": True,
        })
    return variants


def _build_supportive_phase2_fallback(
    supportive_query_defs,
    prior_analysis: str,
    response_text: str,
    already_run_titles=None,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    response_text = response_text or ""
    prior_analysis = prior_analysis or ""
    already_run_titles = already_run_titles or set()
    if not supportive_query_defs:
        return []

    analysis_text = f"{prior_analysis} {response_text}".lower()
    scored_queries = []

    for query_def in supportive_query_defs:
        if isinstance(query_def, dict):
            title = (query_def.get("title") or "").strip()
            spl_query = (query_def.get("spl_query") or query_def.get("spl") or "").strip()
            description = (query_def.get("description") or "").strip()
        else:
            title = (getattr(query_def, "title", "") or "").strip()
            spl_query = (getattr(query_def, "spl_query", "") or "").strip()
            description = (getattr(query_def, "description", "") or "").strip()
        if not title or not spl_query:
            continue

        norm_title = _normalize_phase2_text(title)
        score = 0
        query_text = f"{title} {description} {spl_query}".lower()
        for token in ["host", "user", "process", "parent", "source", "destination", "ip", "timeline", "recent", "auth", "ssh", "root", "outbound", "sysmon", "powershell"]:
            if token in analysis_text and token in query_text:
                score += 2
        if any(token in query_text for token in ["confirm", "validate", "timeline", "recent", "activity"]):
            score += 1
        # Prefer queries not already run with saved results
        if norm_title in already_run_titles:
            score -= 10

        scored_queries.append((score, {
            "title": title,
            "spl": spl_query,
            "description": description or "Use this query to collect disposition-driving follow-up evidence for the current hypothesis.",
        }))

    scored_queries.sort(key=lambda item: item[0], reverse=True)
    # Prefer strictly unused first; if all already run, still return top scored
    unused = [item[1] for item in scored_queries if _normalize_phase2_text(item[1]["title"]) not in already_run_titles]
    if unused:
        return unused[:max_queries]
    return [item[1] for item in scored_queries[:max_queries]]


def _annotate_phase2_targets(phase2_queries, investigation_state):
    """Attach the open question/blocker each grounded query is intended to resolve."""
    state = investigation_state or {}
    questions = [str(item).strip() for item in (state.get("unresolved_questions") or []) if str(item).strip()]
    blockers = [str(item).strip() for item in (state.get("closure_blockers") or []) if str(item).strip()]
    targets = questions + [item for item in blockers if item not in questions and "remain unresolved" not in item.lower()]
    if not targets:
        return phase2_queries
    for query in phase2_queries or []:
        query_text = " ".join([
            str(query.get("title") or ""),
            str(query.get("description") or ""),
            str(query.get("spl") or ""),
        ]).lower()
        ranked = []
        for target in targets:
            words = set(re.findall(r"[a-z0-9]+", target.lower()))
            overlap = sum(1 for word in words if len(word) > 3 and word in query_text)
            ranked.append((overlap, target))
        ranked.sort(key=lambda item: item[0], reverse=True)
        query["target_questions"] = [target for score, target in ranked if score > 0][:2] or targets[:1]
    return phase2_queries


