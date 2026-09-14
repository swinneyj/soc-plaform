"""Rule detection science, supportive query templates, and placeholder resolution service.

Handles correlation rule lookup, supportive playbook queries (Splunk SPL & MDE KQL),
placeholder alias substitution ($host$, $user$, $dest$, etc.), and query validation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_PLACEHOLDER_ALIASES: List[Dict[str, Any]] = [
    {
        "alias": "host",
        "fields": ["host", "destination", "dest", "ComputerName", "DeviceName", "destination_nt_host"],
        "description": "Target endpoint or computer name",
    },
    {
        "alias": "dest",
        "fields": ["destination", "dest", "host", "destination_ip", "dest_ip", "ComputerName", "DeviceName"],
        "description": "Target destination host or IP",
    },
    {
        "alias": "user",
        "fields": ["user", "username", "AccountName", "Account", "src_user", "actor"],
        "description": "Target user account or identity",
    },
    {
        "alias": "src",
        "fields": ["source_ip", "src_ip", "src", "Source Host", "host"],
        "description": "Source IP address or originating host",
    },
    {
        "alias": "process",
        "fields": ["process", "process_name", "FileName", "command_line", "process_exec"],
        "description": "Target process or executable name",
    },
    {
        "alias": "parent_process",
        "fields": ["parent_process", "parent_process_name", "ParentProcessName"],
        "description": "Parent process or spawning executable name",
    },
]


def normalize_rule_name(name: str) -> str:
    """Normalize rule names by stripping common SIEM wrapper tags."""
    if not name:
        return ""
    text = name.strip()
    # Strip common prefixes like "Endpoint - ", "Network - ", "Access - "
    text = re.sub(r"^(?:Endpoint|Network|Access|Cloud|Audit|Identity|Web)\s*-\s*", "", text, flags=re.IGNORECASE)
    # Strip trailing " - Rule", " - Search"
    text = re.sub(r"\s*-\s*(?:Rule|Search|Alert)$", "", text, flags=re.IGNORECASE)
    return text.strip().lower()


def resolve_correlation_rule(db, model_cls, rule_anchor: str):
    """Resolve an ESCorrelationRule by rule_id or fuzzy rule_name."""
    if not rule_anchor:
        return None

    anchor = rule_anchor.strip()
    # 1. Exact rule_id
    rule = db.query(model_cls).filter(model_cls.rule_id == anchor).first()
    if rule:
        return rule

    # 2. Exact rule_name
    rule = db.query(model_cls).filter(model_cls.rule_name == anchor).first()
    if rule:
        return rule

    # 3. Normalized rule_name match
    norm_anchor = normalize_rule_name(anchor)
    all_rules = db.query(model_cls).all()
    for candidate in all_rules:
        if normalize_rule_name(candidate.rule_name) == norm_anchor:
            return candidate
        if candidate.rule_id and candidate.rule_id.lower() == norm_anchor:
            return candidate

    # 4. Substring containment
    for candidate in all_rules:
        cand_norm = normalize_rule_name(candidate.rule_name)
        if norm_anchor in cand_norm or cand_norm in norm_anchor:
            return candidate

    return None


def get_supportive_queries_for_rule(db, query_model_cls, rule_id: str) -> List[Any]:
    """Load all supportive queries for a rule, including family queries."""
    if not rule_id:
        return []

    clean_id = rule_id.strip()
    queries = db.query(query_model_cls).filter(query_model_cls.rule_id == clean_id).all()

    # Living off the Land (LotL) family sharing
    canonical_lotl = "lotl_outbound_connection"
    if clean_id.startswith("lotl_") and clean_id != canonical_lotl:
        lotl_extras = db.query(query_model_cls).filter(query_model_cls.rule_id == canonical_lotl).all()
        existing_titles = {q.title for q in queries}
        for q in lotl_extras:
            if q.title not in existing_titles:
                queries.append(q)

    return queries


def resolve_placeholder_value(
    token_name: str,
    fields: Dict[str, str],
    custom_aliases: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Find a matching value for a placeholder token from notable fields."""
    token = token_name.strip().strip("$%{}").lower()

    # 1. Direct field match
    for k, v in fields.items():
        if k.lower() == token and str(v).strip():
            return str(v).strip()

    # 2. Check custom and default alias lists
    alias_definitions = (custom_aliases or []) + DEFAULT_PLACEHOLDER_ALIASES
    for alias_def in alias_definitions:
        if alias_def.get("alias", "").lower() == token:
            for candidate_field in alias_def.get("fields", []):
                for k, v in fields.items():
                    if k.lower() == candidate_field.lower() and str(v).strip():
                        return str(v).strip()

    return None


def render_query_template(
    template: str,
    fields: Dict[str, str],
    custom_aliases: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[str]]:
    """Substitute $placeholder$ or {placeholder} tokens using notable fields and aliases."""
    if not template:
        return "", []

    rendered = template
    unresolved: List[str] = []

    # Find $token$ and {token} matches
    token_pattern = re.compile(r"(\$([a-zA-Z0-9_]+)\$|\{([a-zA-Z0-9_]+)\})")
    matches = token_pattern.findall(template)

    for full_match, dollar_token, brace_token in matches:
        token_name = dollar_token or brace_token
        resolved_val = resolve_placeholder_value(token_name, fields, custom_aliases)
        if resolved_val is not None:
            rendered = rendered.replace(full_match, resolved_val)
        else:
            if token_name not in unresolved:
                unresolved.append(token_name)

    return rendered, unresolved


def validate_query_readiness(rendered_query: str) -> Dict[str, Any]:
    """Verify whether a rendered query is ready to be executed without missing tokens."""
    unresolved_matches = re.findall(r"(\$[a-zA-Z0-9_]+\$|\{[a-zA-Z0-9_]+\})", rendered_query or "")
    has_unresolved = len(unresolved_matches) > 0
    query_type = "kql" if re.search(r"\bDeviceProcessEvents\b|\bDeviceNetworkEvents\b|\bSecurityAlert\b", rendered_query or "") else "spl"

    return {
        "ready": not has_unresolved,
        "query_type": query_type,
        "unresolved_tokens": list(dict.fromkeys(unresolved_matches)),
        "character_count": len(rendered_query or ""),
    }
