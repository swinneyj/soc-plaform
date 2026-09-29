"""Compact triage key-field extraction for collapsed triage cards."""
from typing import Any, Dict, Optional

# Priority order for compact key-fields shown on collapsed triage cards.
# Only fields that are present and non-empty are included.
TRIAGE_KEY_FIELD_PRIORITY = [
    ("host", "Host"),
    ("destination", "Destination"),
    ("user", "User"),
    ("username", "User"),
    ("ssh_file_path", "SSH File Path"),
    ("file_path", "File Path"),
    ("file_name", "File Name"),
    ("process", "Process"),
    ("parent_process", "Parent Process"),
    ("urgency", "Urgency"),
    ("source_ip", "Source IP"),
    ("destination_ip", "Destination IP"),
    ("destination_port", "Dest Port"),
    ("source_port", "Source Port"),
    ("owner", "Owner"),
    ("severity", "Severity"),
    ("risk_score", "Risk Score"),
]


def _field_lookup(fields: Dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value for any of the given keys (case-insensitive)."""
    if not fields:
        return ""
    # Direct hits first
    for key in keys:
        val = fields.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    # Case-insensitive fallback
    lower_map = {str(k).lower(): v for k, v in fields.items()}
    for key in keys:
        val = lower_map.get(key.lower())
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def extract_triage_key_fields(fields: Dict[str, Any], raw_fields: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Build a compact ordered dict of high-value fields for triage card previews.

    Pulls from canonical parsed fields first, then raw_fields as a fallback so
    rule-specific values (SSH File Path, process, etc.) surface even when the
    original paste used slightly different labels.
    """
    merged: Dict[str, Any] = {}
    if raw_fields and isinstance(raw_fields, dict):
        merged.update(raw_fields)
    if fields and isinstance(fields, dict):
        merged.update(fields)

    result: Dict[str, str] = {}
    seen_labels: set = set()
    host_val = _field_lookup(merged, "host", "Host")
    dest_val = _field_lookup(merged, "destination", "Destination", "dest")

    for key, label in TRIAGE_KEY_FIELD_PRIORITY:
        if label in seen_labels:
            continue
        value = _field_lookup(merged, key)
        if not value:
            continue
        # Skip Destination when it is identical to Host (common on endpoint notables)
        if label == "Destination" and host_val and value.lower() == host_val.lower():
            continue
        # Skip Username duplicate when User already present
        if label == "User" and "User" in seen_labels:
            continue
        result[label] = value
        seen_labels.add(label)
    return result

