"""ES correlation-rule resolution (promote + analyze fuzzy adoption).

`_normalize_rule_match_text` is the shared matcher normalization — the same
canonical function as services.analysis_service.normalize_phase2_text
(lowercase, non-alphanumerics -> spaces)."""
from services.analysis_service import normalize_phase2_text as _normalize_rule_match_text

def _resolve_correlation_rule(db, correlation_model, anchor_text: str):
    anchor = _normalize_rule_match_text(anchor_text)
    if not anchor:
        return None

    rules = db.query(correlation_model).filter(correlation_model.enabled == 1).all()

    for rule in rules:
        name = _normalize_rule_match_text(rule.rule_name or "")
        if name and name == anchor:
            return rule

    for rule in rules:
        name = _normalize_rule_match_text(rule.rule_name or "")
        if not name:
            continue
        if anchor in name or name in anchor:
            return rule

    anchor_tokens = {
        token for token in anchor.split()
        if token not in {"endpoint", "network", "rule", "alert", "detection"}
    }
    if not anchor_tokens:
        return None

    best_rule = None
    best_score = 0
    for rule in rules:
        name_tokens = {
            token for token in _normalize_rule_match_text(rule.rule_name or "").split()
            if token not in {"endpoint", "network", "rule", "alert", "detection"}
        }
        if not name_tokens:
            continue

        overlap = anchor_tokens & name_tokens
        if not overlap:
            continue

        score = len(overlap)
        if anchor_tokens.issubset(name_tokens):
            score += 10

        if score > best_score:
            best_rule = rule
            best_score = score

    return best_rule
