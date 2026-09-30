"""Pasted-notable surface: paste parsing, notable list/detail/delete,
historical ingest, fetch-SPL generation, and closure-note backfill.

Also owns the notable parsing pipeline the paste flow runs end-to-end:
field aliases, section extraction, description inference, parse assessment,
dedup keys, and artifact saving.
"""
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from api import deps

from api.schemas import NotableFetchSplRequest, PastedNotableRequest
from db.util import utcnow_naive

router = APIRouter()

# C2.1.4 hardening (DEVELOPMENT_PLAN §11, signed off Sept 30): cap raw paste
# payload size before any parsing/sanitization work. Over-limit answers 413.
PASTE_MAX_BYTES = int(os.environ.get("PASTE_MAX_BYTES", str(5 * 1024 * 1024)))

def build_notable_fetch_spl(req: "NotableFetchSplRequest") -> Dict[str, Any]:
    """Build production SPL for open notables with a flawless paste_block.

    Strategy (validated against this ES deployment):
      1. incident_review -> current New/Unassigned/In Progress only
         (dedup rule_id; exclude Resolved/Closed)
      2. Left-join index=notable technical fields by normalized rule name
         + nearest time (rule_id is empty on notable events here)
      3. Emit paste_block = Label: value lines for non-empty fields only
         -> copy one cell into the app paste box (parse_structured_notable)
    """
    earliest = (req.earliest or "-30d").strip() or "-30d"
    latest = (req.latest or "now").strip() or "now"
    max_rows = int(req.max_rows or 50)
    notable_index = (req.notable_index or "notable").strip() or "notable"

    extra_review: List[str] = []
    extra_notable: List[str] = []
    if req.rule_name and str(req.rule_name).strip():
        rn = str(req.rule_name).strip().replace('"', '\\"')
        extra_review.append(f'rule_name="*{rn}*"')
        extra_notable.append(f'search_name="*{rn}*"')
    if req.correlation_search and str(req.correlation_search).strip():
        cs = str(req.correlation_search).strip().replace('"', '\\"')
        extra_review.append(f'rule_name="*{cs}*"')
        extra_notable.append(f'search_name="*{cs}*"')
    if req.dest and str(req.dest).strip():
        d = str(req.dest).strip().replace('"', '\\"')
        if not d.endswith("*"):
            d = d + "*"
        extra_notable.append(f'dest="{d}"')
    if req.host and str(req.host).strip():
        h = str(req.host).strip().replace('"', '\\"')
        if not h.endswith("*"):
            h = h + "*"
        extra_notable.append(f'(host="{h}" OR dest="{h}")')

    review_extra = (" ".join(extra_review)).strip()
    notable_extra = (" ".join(extra_notable)).strip()
    review_extra_clause = f" {review_extra}" if review_extra else ""
    notable_extra_clause = f" {notable_extra}" if notable_extra else ""

    # Note: paste_block uses a real newline inside mvjoin so the cell is multi-line Label: value text.
    nl = "\n"
    spl_parts = [
        "| `incident_review`",
        "| sort 0 - _time",
        "| dedup rule_id",
        "| where (status_label=\"New\" OR status_label=\"Unassigned\" OR status_label=\"In Progress\" OR status=0 OR status=1 OR status=2)",
        "    AND status_label!=\"Resolved\"",
        "    AND status_label!=\"Closed\"",
        "    AND status!=4",
        "    AND status!=5",
        f"| search earliest=\"{earliest}\" latest=\"{latest}\"{review_extra_clause}",
        "| rename _time AS review_time",
        "| eval join_rule=lower(trim(rule_name))",
        "| eval key=1",
        "| join type=left max=0 key",
        f"    [ search (index={notable_index}) earliest=\"{earliest}\" latest=\"{latest}\"{notable_extra_clause}",
        "      | eval join_rule=lower(trim(search_name))",
        r"      | eval join_rule=replace(join_rule, \"^endpoint\\s*-\\s*\", \"\")",
        r"      | eval join_rule=replace(join_rule, \"\\s*-\\s*rule$\", \"\")",
        "      | eval notable_time=_time",
        "      | eval key=1",
        "      | table key, join_rule, notable_time, dest, dest_ip, dest_port, src_ip, src_port,",
        "              user, process, count, first_seen, last_seen, search_name, category, host, description",
        "    ]",
        "| where isnull(search_name) OR lower(trim(rule_name))=join_rule",
        "| eval time_diff=if(isnotnull(notable_time), abs(review_time - notable_time), null())",
        "| eventstats min(time_diff) AS min_diff BY rule_id",
        "| where isnull(time_diff) OR time_diff=min_diff",
        "| sort 0 - review_time",
        f"| head {max_rows}",
        "| eval title_val=if(isnotnull(rule_name) AND rule_name!=\"\", rule_name, search_name)",
        "| eval status_val=if(isnotnull(status_label) AND status_label!=\"\", status_label, \"\")",
        "| eval paste_lines=mvappend(",
        "    if(title_val!=\"\", \"Title: \".title_val, null()),",
        "    if(isnotnull(search_name) AND search_name!=\"\", \"Correlation Search: \".search_name, if(isnotnull(rule_name) AND rule_name!=\"\", \"Correlation Search: \".rule_name, null())),",
        "    if(isnotnull(description) AND description!=\"\", \"Description: \".description, null()),",
        "    if(isnotnull(dest) AND dest!=\"\", \"Destination: \".dest, null()),",
        "    if(isnotnull(dest_ip) AND dest_ip!=\"\", \"Destination IP Address: \".dest_ip, null()),",
        "    if(isnotnull(dest_port) AND dest_port!=\"\", \"Destination Port: \".dest_port, null()),",
        "    if(isnotnull(src_ip) AND src_ip!=\"\", \"Source IP Address: \".src_ip, null()),",
        "    if(isnotnull(src_port) AND src_port!=\"\", \"Source Port: \".src_port, null()),",
        "    if(isnotnull(user) AND user!=\"\", \"User: \".user, null()),",
        "    if(isnotnull(process) AND process!=\"\", \"Process: \".process, null()),",
        "    if(isnotnull(count) AND count!=\"\", \"Count: \".count, null()),",
        "    if(isnotnull(first_seen) AND first_seen!=\"\", \"First Seen: \".first_seen, null()),",
        "    if(isnotnull(last_seen) AND last_seen!=\"\", \"Last Seen: \".last_seen, null()),",
        "    if(isnotnull(category) AND category!=\"\", \"Category: \".mvjoin(category, \" \"), null()),",
        "    if(isnotnull(notable_time), \"Time: \".strftime(notable_time, \"%Y-%m-%dT%H:%M:%S\"), null()),",
        "    if(isnotnull(owner) AND owner!=\"\", \"Owner: \".owner, null()),",
        "    if(status_val!=\"\", \"Status: \".status_val, null()),",
        "    if(isnotnull(urgency) AND urgency!=\"\", \"Urgency: \".urgency, null()),",
        "    if(isnotnull(disposition) AND disposition!=\"\" AND NOT match(disposition, \"^disposition:\\d+$\"), \"Disposition: \".disposition, null()),",
        "    if(isnotnull(rule_id) AND rule_id!=\"\", \"Rule ID: \".rule_id, null()),",
        "    \"Type: notable\"",
        "  )",
        "| eval paste_block=mvjoin(paste_lines, \"\n\")",
        "| table",
        "    review_time, status_label, owner, rule_name, dest, dest_ip, dest_port,",
        "    src_ip, src_port, user, process, count, first_seen, last_seen,",
        "    search_name, category, notable_time, time_diff, paste_block",
    ]
    spl = "\n".join(spl_parts)

    paste_hint_lines = [
        "Flawless paste workflow:",
        "1. Run the SPL in Splunk (Statistics view).",
        "2. Click the paste_block cell for the row you want.",
        "3. Copy (Ctrl+C) — already Label: value lines with blanks omitted.",
        "4. Paste into the app Notable paste box and Save.",
        "5. Do NOT copy from the Incident Review detail pane (UI badges corrupt hostnames).",
        "",
        "Status filter: New / Unassigned / In Progress only (Resolved & Closed excluded).",
        "Owner from incident_review; dest/process/IPs from index=notable.",
    ]

    return {
        "spl": spl,
        "earliest": earliest,
        "latest": latest,
        "max_rows": max_rows,
        "notable_index": notable_index,
        "instructions": paste_hint_lines,
        "why": (
            "IR detail-pane copy glues UI badges into fields (NDC36-81 + badge 0 -> NDC36-810). "
            "This query merges open review state with notable-index technical fields and "
            "emits paste_block (Label: value, non-empty only) for one-click paste into the app."
        ),
    }




NOTABLE_FIELD_ALIASES = [
    ("Description", "description"),
    ("Additional FieldsValue", "additional_fields_value"),
    ("Additional Fields Value", "additional_fields_value"),
    ("Added Account", "added_account"),
    ("Actor", "actor"),
    ("Coorelation Search", "correlation_search"),
    ("Correlation Search", "correlation_search"),
    ("Search Name", "correlation_search"),
    ("search_name", "correlation_search"),
    ("Security Domain", "security_domain"),
    ("SSL Errors", "ssl_errors"),
    ("Destination", "destination"),
    ("dest", "destination"),
    ("Destination Business Unit", "destination_business_unit"),
    ("Destination Category", "destination_category"),
    ("Destination DNS", "destination_dns"),
    ("Destination Expected", "destination_expected"),
    ("Destination IP Address", "destination_ip"),
    ("dest_ip", "destination_ip"),
    ("Destination NT Hostname", "destination_nt_hostname"),
    ("dest_nt_host", "destination_nt_hostname"),
    ("Destination PCI Domain", "destination_pci_domain"),
    ("Destination Port", "destination_port"),
    ("dest_port", "destination_port"),
    ("Disposition", "disposition"),
    ("Closure Summary", "closure_summary"),
    ("Closure Notes", "closure_summary"),
    ("Closure Note", "closure_summary"),
    ("Username", "username"),
    ("SSH File Path", "ssh_file_path"),
    ("File Path", "file_path"),
    ("File Name", "file_name"),
    ("Process", "process"),
    ("Parent Process", "parent_process"),
    ("Risk Score", "risk_score"),
    ("Severity", "severity"),
    ("Signature", "signature"),
    ("Urgency", "urgency"),
    ("Status", "status"),
    ("Actions", "actions"),
    ("Action", "action"),
    ("Owner", "owner"),
    ("Title", "title"),
    ("Type", "type"),
    ("Time", "time"),
    ("Host", "host"),
    ("Source", "source_ip"),
    ("Source IP Address", "source_ip"),
    ("src_ip", "source_ip"),
    ("Source Port", "source_port"),
    ("src_port", "source_port"),
    ("User Email", "user_email"),
    ("User First Name", "user_first_name"),
    ("User Last Name", "user_last_name"),
    ("User Identity", "user_identity"),
    ("User Category", "user_category"),
    ("User", "user"),
    ("Event ID", "event_id"),
    ("event_id", "event_id"),
    ("Event Hash", "event_hash"),
    ("event_hash", "event_hash"),
    ("Rule ID", "rule_id"),
    ("rule_id", "rule_id"),
    ("Rule Name", "rule_name"),
    ("rule_name", "rule_name"),
    ("Value", "value"),
    # Process / command (CIM + Sysmon-style)
    ("Command Line", "command_line"),
    ("command_line", "command_line"),
    ("CommandLine", "command_line"),
    ("Parent Image", "parent_image"),
    ("parent_image", "parent_image"),
    ("ParentImage", "parent_image"),
    ("Image", "process"),
    ("Process Name", "process"),
    ("process_name", "process"),
    ("Process Path", "process"),
    ("process_path", "process"),
    # Remote / web
    ("Remote URL", "remote_url"),
    ("remoteURL", "remote_url"),
    ("remote_url", "remote_url"),
    ("URL", "remote_url"),
    ("url", "remote_url"),
    # Aggregation / risk extras
    ("Count", "count"),
    ("count", "count"),
    ("First Seen", "first_seen"),
    ("first_seen", "first_seen"),
    ("Last Seen", "last_seen"),
    ("last_seen", "last_seen"),
    ("VPR Score", "vpr_score"),
    ("vpr_score", "vpr_score"),
    ("Risk Tier", "risk_tier"),
    ("risk_tier", "risk_tier"),
    ("Vuln Count", "vuln_count"),
    ("vuln_count", "vuln_count"),
    ("Category", "category"),
    ("category", "category"),
    ("Zero Trust", "zero_trust"),
]

NOTABLE_FIELD_LABELS = {
    "description": "Description",
    "title": "Title",
    "correlation_search": "Correlation Search",
    "type": "Type",
    "time": "Time",
    "disposition": "Disposition",
    "urgency": "Urgency",
    "status": "Status",
    "owner": "Owner",
    "host": "Host",
    "destination": "Destination",
    "destination_business_unit": "Destination Business Unit",
    "destination_category": "Destination Category",
    "destination_dns": "Destination DNS",
    "destination_expected": "Destination Expected",
    "destination_ip": "Destination IP Address",
    "destination_nt_hostname": "Destination NT Hostname",
    "destination_pci_domain": "Destination PCI Domain",
    "destination_port": "Destination Port",
    "user": "User",
    "username": "Username",
    "user_email": "User Email",
    "user_first_name": "User First Name",
    "user_last_name": "User Last Name",
    "user_identity": "User Identity",
    "user_category": "User Category",
    "source_ip": "Source IP Address",
    "source_port": "Source Port",
    "actions": "Actions",
    "action": "Action",
    "additional_fields_value": "Additional FieldsValue",
    "added_account": "Added Account",
    "actor": "Actor",
    "value": "Value",
    "ssh_file_path": "SSH File Path",
    "file_path": "File Path",
    "file_name": "File Name",
    "process": "Process",
    "parent_process": "Parent Process",
    "parent_image": "Parent Image",
    "command_line": "Command Line",
    "remote_url": "Remote URL",
    "count": "Count",
    "first_seen": "First Seen",
    "last_seen": "Last Seen",
    "vpr_score": "VPR Score",
    "risk_tier": "Risk Tier",
    "vuln_count": "Vuln Count",
    "category": "Category",
    "zero_trust": "Zero Trust",
    "risk_score": "Risk Score",
    "security_domain": "Security Domain",
    "ssl_errors": "SSL Errors",
    "severity": "Severity",
    "signature": "Signature",
    "event_id": "Event ID",
    "event_hash": "Event Hash",
    "rule_id": "Rule ID",
    "rule_name": "Rule Name",
}

EMBEDDED_FIELD_EXTRACTORS = [
    ("correlation_search", ["Coorelation Search", "Correlation Search"]),
    ("signature", ["Signature"]),
    ("ssl_errors", ["SSL Errors"]),
    ("risk_score", ["Risk Score"]),
]

GLUED_FIELD_TAIL_MARKERS = [
    "Risk Score",
    "SSL Errors",
    "Severity",
    "Urgency",
    "Status",
    "Owner",
    "Disposition",
    "Security Domain",
    "Source Port",
    "Destination Port",
    "Time",
    "Title",
    "Type",
]


def trim_glued_field_tails(value: str, tail_markers: Optional[List[str]] = None) -> str:
    """Trim known field labels that were accidentally glued onto a value."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""

    markers = tail_markers or GLUED_FIELD_TAIL_MARKERS
    while cleaned:
        lower = cleaned.lower()
        candidate_indexes = []
        for marker in markers:
            idx = lower.find(marker.lower())
            if idx <= 0:
                continue
            prev_char = cleaned[idx - 1]
            if prev_char.isalnum() or prev_char in ")].":
                candidate_indexes.append(idx)
        if not candidate_indexes:
            break
        cleaned = cleaned[:min(candidate_indexes)].strip()

    return cleaned


def is_usable_primary_entity(key: str, value: str) -> bool:
    """Return True when a parsed anchor looks concrete enough for analysis."""
    candidate = (value or "").strip()
    if not candidate:
        return False

    if "[0](http" in candidate or "####" in candidate:
        return False

    if trim_glued_field_tails(candidate) != candidate:
        return False

    if key in {"source_ip", "destination_ip"}:
        return bool(
            re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", candidate)
            or re.fullmatch(r"[A-Fa-f0-9:]+", candidate)
            or re.fullmatch(r"[A-Z0-9_]+", candidate)
        )

    if key in {"host", "destination"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", candidate) or re.fullmatch(r"[A-Z0-9_]+", candidate))

    if key in {"user", "username"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_@.\\:-]+", candidate) or re.fullmatch(r"[A-Z0-9_]+", candidate))

    if key in {"process", "parent_process"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_.:\\/-]+", candidate))

    return True


def parse_pasted_notable(raw_text: str) -> Dict[str, str]:
    """Extract common Splunk notable key/value pairs from pasted text."""
    matches = []
    normalized = normalize_pasted_text(raw_text or "")

    for alias, canonical in sorted(NOTABLE_FIELD_ALIASES, key=lambda item: len(item[0]), reverse=True):
        # For certain aliases like User/Process, only treat them as field
        # labels when they appear at the beginning of a line. This avoids
        # mis-parsing natural-language sentences such as "The suspicious
        # process executed by user has initiated connections to an external
        # IP." where "process"/"user" are ordinary words, not headings.
        alias_lower = alias.lower()
        if canonical in {"user", "process"} and alias_lower in {"user", "process"}:
            pattern = re.compile(r"(?m)^\s*" + re.escape(alias) + r"\b", flags=re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(alias), flags=re.IGNORECASE)

        for match in pattern.finditer(normalized):
            matches.append({
                "start": match.start(),
                "end": match.end(),
                "canonical": canonical,
            })

    matches.sort(key=lambda item: (item["start"], -(item["end"] - item["start"])))

    deduped = []
    current_end = -1
    for match in matches:
        if match["start"] < current_end:
            continue
        deduped.append(match)
        current_end = match["end"]

    parsed: Dict[str, str] = {}
    for index, match in enumerate(deduped):
        value_start = match["end"]
        value_end = deduped[index + 1]["start"] if index + 1 < len(deduped) else len(normalized)
        value = normalized[value_start:value_end].strip(" \t:\n")

        # If another known label appears inside the value span (common in
        # glued-together exports like "Owner dalton lewis Security Domain
        # network"), truncate at the earliest such label so each field keeps
        # only its own value instead of swallowing subsequent labels.
        #
        # Use whole-word matching so aliases like "Host" do NOT match inside
        # words like "hosts", which would incorrectly chop values such as
        # "ndc24-1 session hosts west-24 ..." down to just "ndc24-1 session".
        lower_value = value.lower()
        earliest_alias_idx = None
        for alias, _ in NOTABLE_FIELD_ALIASES:
            alias_lower = alias.lower()
            pattern = re.compile(r"\b" + re.escape(alias_lower) + r"\b")
            m = pattern.search(lower_value)
            if not m:
                continue
            idx = m.start()
            if earliest_alias_idx is None or idx < earliest_alias_idx:
                earliest_alias_idx = idx
        if earliest_alias_idx is not None and earliest_alias_idx > 0:
            value = value[:earliest_alias_idx]

        value = re.sub(r"\s+", " ", value).strip()
        if value and match["canonical"] not in parsed:
            parsed[match["canonical"]] = value

    return parsed


def extract_notable_section(raw_text: str, heading: str, end_markers: Optional[List[str]] = None) -> str:
    """Extract a markdown-style section from pasted Incident Review text."""
    if not raw_text:
        return ""

    normalized = raw_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    def normalize_heading_label(line: str) -> str:
        return re.sub(r"^#+\s*", "", line).strip().lower()

    heading_norm = heading.strip().lower()
    start_index = None
    for index, line in enumerate(lines):
        if normalize_heading_label(line) == heading_norm:
            start_index = index
            break

    if start_index is None or start_index + 1 >= len(lines):
        return ""

    normalized_markers = [m.strip().lower() for m in (end_markers or [])]
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        lower = normalize_heading_label(lines[index])
        if any(lower.startswith(marker) for marker in normalized_markers):
            end_index = index
            break

    return "\n".join(lines[start_index + 1:end_index]).strip()


def infer_notable_fields_from_description(fields: Dict[str, str]) -> Dict[str, str]:
    """Infer a few high-value fields from the freeform description section."""
    description = (fields.get("description") or "").strip()
    if not description:
        return fields

    if not (fields.get("user") or fields.get("username")):
        user_match = re.search(r"\bUser:\s*([^\.\n]+)", description, flags=re.IGNORECASE)
        if user_match:
            fields["username"] = user_match.group(1).strip()

    existing_user = (fields.get("user") or fields.get("username") or "").strip()
    if existing_user and (" experienced " in existing_user.lower() or "observed source ips" in existing_user.lower()):
        acct_match = re.search(r"account\s+([^\s\.]+)", existing_user, flags=re.IGNORECASE)
        if acct_match:
            account_value = acct_match.group(1).strip()
            fields["user"] = account_value
            fields["username"] = account_value

    if not (fields.get("source_ip") or "").strip():
        src_match = re.search(r"Observed\s+Source\s+IPs?:\s*([^\.\s]+)", description, flags=re.IGNORECASE)
        if src_match:
            fields["source_ip"] = src_match.group(1).strip()

    if not fields.get("host"):
        host_match = re.search(r"\bon\s+([^\s\[]+)\s*\[", description, flags=re.IGNORECASE)
        if host_match:
            host_value = host_match.group(1).strip()
            if "$" not in host_value:
                fields["host"] = host_value

    if not fields.get("host"):
        host_match = re.search(r"\bon\s+host\s+([^\s\.]+(?:\.[^\s\.]+)*)", description, flags=re.IGNORECASE)
        if host_match:
            host_value = host_match.group(1).strip().rstrip('.')
            if host_value:
                fields["host"] = host_value

    if not fields.get("destination") and fields.get("host"):
        fields["destination"] = fields["host"]

    if not fields.get("process"):
        child_match = re.search(r"spawned\s+([^\n]+?)\s*\(parent:", description, flags=re.IGNORECASE)
        if child_match:
            child_value = child_match.group(1).strip()
            child_basename = re.split(r"[\\/]", child_value)[-1].strip()
            if child_basename:
                fields["process"] = child_basename

    if not fields.get("parent_process"):
        parent_match = re.search(r"\(parent:\s*([^\)]+)\)", description, flags=re.IGNORECASE)
        if parent_match:
            parent_value = parent_match.group(1).strip()
            parent_basename = re.split(r"[\\/]", parent_value)[-1].strip()
            if parent_basename:
                fields["parent_process"] = parent_basename

    if not fields.get("actor"):
        actor_match = re.search(
            r"The\s+account\s+([^\s\(]+)(?:\s*\([^\)]*\))?\s+added\s+",
            description,
            flags=re.IGNORECASE,
        )
        if actor_match:
            fields["actor"] = actor_match.group(1).strip()

    if not fields.get("added_account"):
        added_match = re.search(
            r"\sadded\s+(.+?)\s+to\s+the\s+administrators\s+group",
            description,
            flags=re.IGNORECASE,
        )
        if added_match:
            fields["added_account"] = added_match.group(1).strip()

    return fields


def normalize_notable_fields(fields: Dict[str, str]) -> Dict[str, str]:
    """Apply small, conservative fix-ups to parsed notable fields."""

    # Current behaviors:
    # - If Destination NT Hostname is present and Destination looks merged or
    #   empty, prefer Destination NT Hostname as the Destination value.
    # - If a field value accidentally captured the "Event Details" section,
    #   trim everything from the first "Event Details" occurrence onward.
    # - If Destination's value still contains "Risk Score" (e.g.,
    #   "Destination NDC56-10Risk Score"), trim at that marker to recover
    #   the hostname.

    # Strip trailing markdown heading artifacts (for example `low ####`) that
    # can appear when a pasted field is immediately followed by a `####`
    # section marker in the source Splunk export.
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        cleaned = re.sub(r"\s+#+\s*$", "", value).strip()
        fields[key] = cleaned

    # Remove markdown link wrappers and obvious inline link tails that often
    # appear in Splunk Incident Review exports, e.g. Host140.18.228.2[0](...)Risk Score
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        cleaned = re.sub(r"\[[^\]]*\]\([^\)]*\)", "", value)
        cleaned = cleaned.replace("(Opens new window)", "")
        fields[key] = re.sub(r"\s+", " ", cleaned).strip()

    # Some exports glue the next field label directly onto the previous
    # field's value. Pull those embedded labels back out conservatively.
    for source_key, value in list(fields.items()):
        if not isinstance(value, str) or not value:
            continue

        current_value = value
        for target_key, aliases in EMBEDDED_FIELD_EXTRACTORS:
            if target_key == source_key:
                continue
            if fields.get(target_key):
                continue

            for alias in aliases:
                pattern = re.compile(rf"\s*{re.escape(alias)}\s*(.+)$", flags=re.IGNORECASE)
                match = pattern.search(current_value)
                if not match:
                    continue

                prefix = current_value[:match.start()].strip()
                suffix = match.group(1).strip()
                if prefix:
                    fields[source_key] = prefix
                if suffix:
                    fields[target_key] = suffix
                current_value = fields[source_key]
                break

    additional_fields = (fields.get("additional_fields_value") or "").strip()
    if additional_fields:
        if not fields.get("actor"):
            actor_match = re.search(r"ActionActor\s*([^\s]+)", additional_fields, flags=re.IGNORECASE)
            if actor_match:
                fields["actor"] = actor_match.group(1).strip()

        if not fields.get("added_account"):
            added_match = re.search(r"Added Account\s*(.+?)(?=\s*Category(?:Other)?\b|\s*Zero Trust\b|$)", additional_fields, flags=re.IGNORECASE)
            if added_match:
                fields["added_account"] = added_match.group(1).strip()

    dest_val = (fields.get("destination") or "").strip()
    if not dest_val or dest_val.lower() in {"business", "category", "dns", "expected", "nt", "pci"}:
        host_from_desc = (fields.get("host") or "").strip()
        if host_from_desc:
            fields["destination"] = host_from_desc

    for key in ["host", "source_ip", "destination", "destination_ip"]:
        entity_value = (fields.get(key) or "").strip()
        if entity_value:
            fields[key] = trim_glued_field_tails(entity_value)

    sev_val = (fields.get("severity") or "").strip()
    if sev_val:
        severity_token = sev_val.split()[0].strip().lower()
        if severity_token in {"low", "medium", "high", "critical"}:
            fields["severity"] = severity_token

    # Normalize destination from destination_nt_hostname when appropriate.
    dest_nt = (fields.get("destination_nt_hostname") or "").strip()
    if dest_nt:
        dest = (fields.get("destination") or "").strip()
        dest_nt_norm = dest_nt.lower()
        dest_norm = dest.lower()
        # If destination is missing OR clearly contains the NT hostname with
        # extra suffix characters (case-insensitive), prefer the cleaner
        # hostname value.
        if not dest or (dest_nt_norm in dest_norm and len(dest) > len(dest_nt)):
            fields["destination"] = dest_nt

    # Trim any accidental inclusion of "Event Details" noise from values.
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        idx = value.find("Event Details")
        if idx != -1:
            cleaned = value[:idx].strip()
            fields[key] = cleaned

    # If destination still contains a concatenated "Risk Score" label,
    # trim it off to recover the hostname.
    dest_val = fields.get("destination") or ""
    rs_idx = dest_val.lower().find("risk score")
    if rs_idx != -1:
        fields["destination"] = dest_val[:rs_idx].strip()

    # Defensive fallback: if Destination/Host looks like a hostname followed by a
    # bare integer (e.g., "NDC45-790 80" or "NDC36-81 0" where the trailing
    # number is a risk score / UI badge), drop the trailing number and keep
    # just the host portion. Applies to space-separated cases only — glued
    # no-space badges (NDC36-810) cannot be disambiguated from legitimate
    # hostnames that end in 0 (NDC45-790) without external inventory, so
    # prefer the | incident_review SPL fetch path for those pastes.
    for host_key in ("destination", "host", "destination_nt_hostname"):
        val = (fields.get(host_key) or "").strip()
        if not val:
            continue
        host_num_match = re.match(r"^([A-Za-z0-9._-]+)\s+\d{1,3}$", val)
        if host_num_match:
            fields[host_key] = host_num_match.group(1)

    # If host/source/destination IP values were masked, keep them if they are
    # still single-token placeholders, but reject obviously merged artifacts.
    for key in ["host", "source_ip", "destination_ip", "destination"]:
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        # Strip residual markdown counters like trailing [0].
        value = re.sub(r"\[\d+\]$", "", value).strip()
        fields[key] = value

    # When the process field contains a long analytic sentence plus an
    # embedded Windows executable path (common in Incident Review exports
    # like "Outbound Connection - Rule ... c:\\windows\\...\\powershell.exe"),
    # prefer the concrete executable path as the process value.
    proc_val = (fields.get("process") or "").strip()
    if proc_val:
        win_path_match = re.search(r"[A-Za-z]:\\\\[^\s]+", proc_val)
        if win_path_match:
            fields["process"] = win_path_match.group(0)

    return fields


def build_generic_enrichment_queries(fields: Dict[str, str]) -> List[Dict[str, str]]:
    """Build a small, safe set of generic SPL queries with concrete values only."""
    queries: List[Dict[str, str]] = []
    correlation_search = (fields.get("correlation_search") or "").strip()
    host = (fields.get("host") or "").strip()
    user = (fields.get("user") or fields.get("username") or "").strip()
    process = (fields.get("process") or "").strip()
    parent_process = (fields.get("parent_process") or "").strip()

    if correlation_search:
        corr_escaped = correlation_search.replace('"', '\\"')
        queries.append({
            "title": "Recent cases for this rule",
            "spl": f'| `incident_review` | search correlation_search="{corr_escaped}" | table _time rule_name correlation_search urgency status owner disposition | sort - _time',
            "description": "Show recent notables for the same correlation search to quickly compare expected versus unusual outcomes.",
        })

    if host:
        host_escaped = host.replace('"', '\\"')
        queries.append({
            "title": "Host activity around alert time",
            "spl": f'search index=* host="{host_escaped}" earliest=-30m latest=+30m | sort 0 _time | table _time host sourcetype source user process Image ParentImage CommandLine',
            "description": "Build a short timeline around the impacted host to see what else was happening nearby.",
        })

    if user:
        user_escaped = user.replace('"', '\\"')
        queries.append({
            "title": "User activity around alert time",
            "spl": f'search index=* earliest=-30m latest=+30m (user="{user_escaped}" OR username="{user_escaped}" OR Account_Name="{user_escaped}") | sort 0 _time | table _time host user sourcetype process Image ParentImage CommandLine',
            "description": "Check what else the same user was doing around the alert window.",
        })

    if process or parent_process:
        clauses = []
        if parent_process:
            clauses.append(f'ParentImage="*\\\\{parent_process}"')
        if process:
            clauses.append(f'Image="*\\\\{process}"')
        if clauses:
            query = 'search index=windows source="XmlWinEventLog:Microsoft-Windows-Sysmon/Operational" EventCode=1 ' + ' '.join(clauses) + ' | table _time ComputerName User ParentImage Image CommandLine ParentCommandLine | sort - _time'
            queries.append({
                "title": "Process lineage validation",
                "spl": query,
                "description": "Validate whether the observed parent and child process relationship is common or suspicious.",
            })

    if not queries:
        queries.append({
            "title": "Recent endpoint process notables",
            "spl": '| `incident_review` | search security_domain=endpoint | table _time correlation_search rule_name urgency status owner disposition | sort - _time | head 25',
            "description": "Start broad: review recent endpoint notables to find the closest comparable activity when the paste is too thin to anchor on a host or user.",
        })

    return queries[:3]


def build_parse_assessment(fields: Dict[str, str], sanitized_text: str, history_text: str) -> Dict[str, Any]:
    """Score how usable a pasted notable is and choose the next workflow mode."""
    score = 0
    missing: List[str] = []

    title = (fields.get("title") or "").strip()
    correlation_search = (fields.get("correlation_search") or "").strip()
    time_value = (fields.get("time") or "").strip()
    primary_entity_keys = ["host", "destination", "destination_ip", "source_ip", "user", "username", "process", "parent_process"]
    identity_context_keys = ["user", "username", "process", "parent_process", "actor", "added_account"]
    clean_primary_entity_keys = [
        key for key in primary_entity_keys if is_usable_primary_entity(key, (fields.get(key) or "").strip())
    ]
    has_primary_entity = bool(clean_primary_entity_keys)
    has_identity_context = any((fields.get(key) or "").strip() for key in identity_context_keys)
    has_context = bool(history_text.strip())
    has_status_bundle = any((fields.get(key) or "").strip() for key in ["disposition", "status", "severity", "urgency"])
    has_detail = bool((fields.get("description") or "").strip() or (sanitized_text or "").strip())
    description = (fields.get("description") or "").strip()
    signature = (fields.get("signature") or "").strip()
    ssl_errors = (fields.get("ssl_errors") or "").strip()
    symptom_text = " ".join([title, correlation_search, description, signature, ssl_errors])
    is_symptom_only_network_case = bool(
        re.search(r"\b(ssl|tls|cipher|handshake|scan(?:ning)?|enumeration|misconfigured client|connection errors?)\b", symptom_text, flags=re.IGNORECASE)
    )

    malformed_keys = []
    for key in ["correlation_search", "host", "destination", "source_ip", "destination_ip", "user", "username", "severity", "urgency", "signature", "time"]:
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        if key in primary_entity_keys:
            if not is_usable_primary_entity(key, value):
                malformed_keys.append(key)
            continue
        if key == "time":
            if re.search(r"\b(HOST|IPV4|USER|REDACTED)_[A-Za-z0-9]+", value):
                malformed_keys.append(key)
            continue
        if any(token in value for token in ["[0](http", "Risk Score", "SSL Errors", "Signature"]) or "####" in value:
            malformed_keys.append(key)

    if correlation_search or (title and title.lower() != "pasted splunk notable"):
        score += 25
    else:
        missing.append("rule identity")

    if time_value:
        score += 20
    else:
        missing.append("time")

    if has_primary_entity:
        score += 20
    else:
        missing.append("primary entity")

    if has_context:
        score += 10
    else:
        missing.append("history or analyst context")

    if has_status_bundle:
        score += 10
    else:
        missing.append("disposition or severity")

    if has_detail:
        score += 15
    else:
        missing.append("supporting detail")

    if malformed_keys:
        score -= min(35, 10 + (5 * len(set(malformed_keys))))
        missing.append("clean field boundaries")

    needs_confirmatory_context = is_symptom_only_network_case and not has_identity_context
    if needs_confirmatory_context:
        score -= 20
        missing.append("confirmatory context")

    generic_title = not title or title.strip().lower() == "pasted splunk notable"
    hard_trigger = generic_title or not time_value or not has_primary_entity or bool(malformed_keys) or needs_confirmatory_context

    if score >= 70 and not hard_trigger:
        mode = "normal"
    elif score >= 40 or correlation_search or title:
        mode = "enrichment"
    else:
        mode = "extraction"

    score = max(0, min(100, score))

    generic_queries = build_generic_enrichment_queries(fields) if mode != "normal" else []

    return {
        "score": score,
        "mode": mode,
        "missing_anchors": missing,
        "generic_queries": generic_queries,
    }


def normalize_pasted_text(raw_text: str) -> str:
    """Normalize paste text so parsers always see real newlines.

    Splunk table-cell copies and some exports often deliver the two-character
    sequences ``\\n`` / ``\\r\\n`` instead of actual line breaks. Without this
    step, line-oriented field parsing collapses and values keep trailing ``\\n``.
    """
    if not raw_text:
        return ""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    real_newlines = text.count("\n")
    literal_markers = text.count("\\n")
    # Expand escape sequences when the paste is clearly flattened
    if literal_markers > 0 and (real_newlines <= 2 or literal_markers >= real_newlines):
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    while "\\\\n" in text:
        text = text.replace("\\\\n", "\n")
    return text


def split_pasted_notables(raw_text: str) -> List[str]:
    """Split a bulk paste that may contain multiple notables into segments.

    Heuristic: treat each line starting with a primary heading ("Title" or
    "Correlation Search") as the beginning of a new notable block. This
    matches the common Splunk Incident Review copy/paste format where each
    notable starts with its own Title/Correlation Search section.
    """
    if not raw_text or not raw_text.strip():
        return []

    normalized = normalize_pasted_text(raw_text)
    lines = normalized.split("\n")

    # Primary heuristic: each card contains a standalone "Notable" line near the top.
    # Use a case-sensitive match so we only pick up the top-of-card
    # "Notable" heading, not the lower-case "notable" line under
    # Event Details.
    notable_pattern = re.compile(r"^\s*Notable\s*$")
    boundaries: List[int] = [
        index for index, line in enumerate(lines) if notable_pattern.match(line)
    ]

    # Fallback heuristic: if we find *no* standalone "Notable" headings at
    # all, we can optionally fall back to Title/Correlation Search labels.
    # However, to avoid over-splitting a single closed Incident Review card
    # into multiple segments (e.g., one for the card and one for a related
    # underlying notable), we only treat this as a multi-notable paste when
    # there are at least three such headings. For one or two headings, keep
    # the entire paste as a single segment.
    if len(boundaries) == 0:
        heading_pattern = re.compile(r"^\s*(Title|Correlation Search)\b", re.IGNORECASE)
        boundaries = [
            index for index, line in enumerate(lines) if heading_pattern.match(line)
        ]
        if len(boundaries) <= 2:
            single = normalized.strip()
            return [single] if single else []

        # Saved single-card artifacts often contain exactly one structured
        # "Correlation Search:" line and one later "Title:" line. That should
        # stay one notable instead of being split into two fragments.
        title_count = sum(1 for line in lines if re.match(r"^\s*Title\b", line, flags=re.IGNORECASE))
        corr_count = sum(1 for line in lines if re.match(r"^\s*(Coorelation Search|Correlation Search)\b", line, flags=re.IGNORECASE))
        if title_count <= 1 and corr_count <= 1:
            single = normalized.strip()
            return [single] if single else []

    # If we still didn't find multiple headings, treat the whole paste as a single notable.
    if len(boundaries) <= 1:
        single = normalized.strip()
        return [single] if single else []

    segments: List[str] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        segment_lines = lines[start:end]
        segment = "\n".join(segment_lines).strip()
        if segment:
            segments.append(segment)

    # Fallback: if something went wrong, at least return the whole text once.
    if not segments:
        single = normalized.strip()
        return [single] if single else []

    return segments


def extract_notable_history(raw_text: str) -> str:
    """Extract the History/closure-notes section from a single notable block.

    We look for a line that is exactly "History" (case-insensitive) and then
    consume subsequent lines until we hit a known boundary marker such as
    "View all review activity", "Drill-down Search", "Adaptive Responses",
    or "Next Steps". This mirrors how Splunk ES renders review history in the
    Incident Review UI.
    """
    if not raw_text:
        return ""

    normalized = raw_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    def normalize_heading_label(line: str) -> str:
        return re.sub(r"^#+\s*", "", line).strip().lower()

    start_index = None
    for index, line in enumerate(lines):
        if normalize_heading_label(line) == "history":
            start_index = index
            break

    if start_index is None or start_index + 1 >= len(lines):
        return ""

    end_markers = (
        "view all review activity",
        "drill-down search",
        "adaptive responses",
        "next steps",
    )

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        lower = normalize_heading_label(lines[index])
        if any(lower.startswith(marker) for marker in end_markers):
            end_index = index
            break

    history_lines = lines[start_index + 1:end_index]
    history_text = "\n".join(history_lines).strip()
    return history_text


def parse_structured_notable(raw_text: str) -> Dict[str, str]:
    """Parse line-oriented notable text in the form `Label: value`."""
    alias_map = {alias.lower(): canonical for alias, canonical in NOTABLE_FIELD_ALIASES}
    labels = sorted(alias_map.keys(), key=len, reverse=True)
    pattern = re.compile(rf"^\s*({'|'.join(re.escape(label) for label in labels)})\s*:\s*(.*)$", re.IGNORECASE)

    parsed: Dict[str, str] = {}
    normalized = normalize_pasted_text(raw_text or "")
    for line in normalized.split("\n"):
        match = pattern.match(line)
        if not match:
            continue

        alias = match.group(1).lower()
        # Strip residual escape junk and collapse internal whitespace
        value = match.group(2).replace("\\n", " ").replace("\\t", " ")
        value = re.sub(r"\s+", " ", value).strip()
        canonical = alias_map.get(alias)
        if canonical and value and canonical not in parsed:
            parsed[canonical] = value

    return parsed


def render_notable_fields(parsed_fields: Dict[str, str]) -> str:
    ordered_keys = [label[1] for label in NOTABLE_FIELD_ALIASES]
    seen = set()
    lines = []
    for key in ordered_keys:
        if key in seen or key not in parsed_fields:
            continue
        seen.add(key)
        label = NOTABLE_FIELD_LABELS.get(key, key.replace("_", " ").title())
        lines.append(f"{label}: {parsed_fields[key]}")

    if not lines:
        return ""

    return "\n".join(lines)



def parse_notable_timestamp(value: str):
    if not value:
        return utcnow_naive()

    candidate = value.strip()
    try:
        return datetime.datetime.fromisoformat(candidate)
    except ValueError:
        try:
            if len(candidate) > 5 and candidate[-3] == ':':
                compact_offset = candidate[:-3] + candidate[-2:]
                return datetime.datetime.strptime(compact_offset, "%Y-%m-%dT%H:%M:%S.000%z")
        except ValueError:
            pass

    return utcnow_naive()


def build_notable_dedup_key(fields: Dict[str, str], sanitized_text: str = "") -> Optional[str]:
    """Build a stable key for deduplicating pasted notables (open or historical).

    Preference order:
      1. Splunk/ES rule_id (unique per notable instance, e.g. ...@@notable@@hash)
      2. event_id / event_hash when present
      3. Composite: correlation/title + time + host/dest + user

    Sanitized text is intentionally excluded so re-pastes with different
    tokenization still match.
    """
    if not fields:
        return None

    rule_id = (fields.get("rule_id") or "").strip()
    if rule_id:
        return f"rule_id:{rule_id}"

    event_id = (fields.get("event_id") or "").strip()
    if event_id:
        return f"event_id:{event_id}"

    event_hash = (fields.get("event_hash") or "").strip()
    if event_hash:
        return f"event_hash:{event_hash}"

    title = (fields.get("title") or fields.get("rule_name") or "").strip()
    corr = (fields.get("correlation_search") or fields.get("rule_name") or "").strip()
    time_val = (fields.get("time") or "").strip()
    host_val = (fields.get("host") or fields.get("destination") or "").strip()
    user_val = (fields.get("user") or fields.get("username") or "").strip()

    anchor = corr or title
    if not anchor:
        return None

    return "|".join([anchor, time_val, host_val, user_val]) or None


# Backwards-compatible alias used by any older call sites
def build_historical_dedup_key(fields: Dict[str, str], sanitized_text: str) -> Optional[str]:
    return build_notable_dedup_key(fields, sanitized_text)


def save_notable_artifacts(platform_root: str, sanitized_text: str, mapping: Dict[str, str], parsed_fields: Dict[str, str]) -> Dict[str, str]:
    active_dir = os.path.join(platform_root, "Data", "Active_Workspace")
    os.makedirs(active_dir, exist_ok=True)

    timestamp = utcnow_naive().strftime("%Y%m%d_%H%M%S")
    text_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.txt")
    fields_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.fields.json")
    mapping_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.map.json")

    with open(text_path, "w", encoding="utf-8") as text_file:
        text_file.write(sanitized_text)

    with open(fields_path, "w", encoding="utf-8") as fields_file:
        json.dump(parsed_fields, fields_file, indent=2)

    with open(mapping_path, "w", encoding="utf-8") as mapping_file:
        json.dump(mapping, mapping_file, indent=2)

    latest_text = os.path.join(active_dir, "Pasted_Notable_latest.txt")
    latest_fields = os.path.join(active_dir, "Pasted_Notable_latest.fields.json")
    latest_map = os.path.join(active_dir, "Pasted_Notable_latest.map.json")

    for source_path, dest_path in ((text_path, latest_text), (fields_path, latest_fields), (mapping_path, latest_map)):
        try:
            import shutil
            shutil.copy2(source_path, dest_path)
        except Exception:
            pass

    return {
        "sanitized_text_path": text_path,
        "fields_path": fields_path,
        "mapping_path": mapping_path,
    }


def serialize_recent_notable(event) -> Dict[str, Any]:
    payload = {}
    try:
        payload = json.loads(event.raw) if event.raw else {}
    except Exception:
        payload = {"sanitized_text": event.raw}

    fields = payload.get("fields", {})
    raw_fields = payload.get("raw_fields") or fields
    parse_assessment = payload.get("parse_assessment") or build_parse_assessment(
        raw_fields,
        payload.get("sanitized_text", ""),
        payload.get("history") or "",
    )
    hidden_from_recent = payload.get("hidden_from_recent", False)
    return {
        "id": event.id,
        "promoted_case_id": payload.get("promoted_case_id"),
        "promoted_at": payload.get("promoted_at"),
        "title": fields.get("title") or event.source,
        "correlation_search": fields.get("correlation_search"),
        "type": fields.get("type"),
        "time": fields.get("time") or (event.timestamp.isoformat() if event.timestamp else None),
        "disposition": fields.get("disposition"),
        "urgency": fields.get("urgency"),
        "status": fields.get("status"),
        "owner": fields.get("owner"),
        "host": fields.get("host") or event.host,
        "destination": fields.get("destination"),
        "user": fields.get("user"),
        "username": fields.get("username"),
        "actions": fields.get("actions") or fields.get("action"),
        "severity": fields.get("severity"),
        "historical": payload.get("historical", False),
        "history": payload.get("history"),
        "parse_assessment": parse_assessment,
        "raw_fields": raw_fields,
        "fields": fields,
        "sanitized_text": payload.get("sanitized_text", ""),
        "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
        "hidden_from_recent": hidden_from_recent,
    }





@router.post("/api/db/notables/generate-fetch-spl", tags=["Database"])
def generate_notable_fetch_spl(request: NotableFetchSplRequest):
    """Generate SPL to pull clean notable fields from Splunk.

    Primary query uses the notable index (works when `incident_review` is
    empty for the analyst role). Response also includes spl_incident_review
    as a secondary option.

    Prefer this over copying the Incident Review detail pane — UI badges
    (red count chips, risk scores) frequently get glued into hostnames
    (e.g. NDC36-81 + badge 0 → NDC36-810).

    Workflow:
      1. Call this endpoint with whatever identifiers you know.
      2. Run the returned `spl` in Splunk.
      3. Copy the Statistics row as Label: value lines.
      4. Paste into the normal notable paste box.
    """
    try:
        return build_notable_fetch_spl(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/db/notables/generate-fetch-spl", tags=["Database"])
def generate_notable_fetch_spl_get(
    correlation_search: Optional[str] = Query(None),
    rule_name: Optional[str] = Query(None),
    dest: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    earliest: str = Query("-7d"),
    latest: str = Query("now"),
    max_rows: int = Query(20, ge=1, le=200),
    notable_index: str = Query("notable"),
):
    """GET convenience wrapper for generate_notable_fetch_spl."""
    req = NotableFetchSplRequest(
        correlation_search=correlation_search,
        rule_name=rule_name,
        dest=dest,
        host=host,
        user=user,
        event_id=event_id,
        rule_id=rule_id,
        earliest=earliest,
        latest=latest,
        max_rows=max_rows,
        notable_index=notable_index,
    )
    return generate_notable_fetch_spl(req)


@router.post("/api/db/notables/paste", tags=["Database"])
def paste_notable(request: PastedNotableRequest):
    """Parse, sanitize, and store a pasted Splunk notable in the database."""
    if len(request.raw_text or "") > PASTE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Paste payload exceeds {PASTE_MAX_BYTES} bytes",
        )
    raw_text = normalize_pasted_text(request.raw_text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="No notable text was provided")

    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent
        from text_sanitizer_pipeline.text_sanitizer_pipeline import sanitize_logs_with_tokens, sanitize_pii_phi

        # C4 (paste-batch adapter): the paste is admitted through the boundary
        # FIRST, so every paste is an inspectable, purgeable batch. The
        # pipeline below is byte-for-byte unchanged — the manifest is the
        # linkage, and its bookkeeping is written after the inserts commit.
        from services import splunk_boundary
        paste_manifest = splunk_boundary.admit_text(raw_text)

        segments = split_pasted_notables(raw_text)
        if not segments:
            raise HTTPException(status_code=400, detail="Unable to detect any notable segments in the pasted text")

        db = SessionLocal()

        events_info = []
        total_mapping_entries = 0
        added_count = 0
        skipped_count = 0
        skipped_segments: List[str] = []
        pending_events: List[Any] = []

        # Always load existing pasted notables for dedup (open + historical).
        # Prefer rule_id / event_id / event_hash; fall back to composite key.
        existing_by_key: Dict[str, Any] = {}
        existing_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

        # Preload existing triage case ids so orphaned promotions do not block re-paste.
        from db.models import TriageResult as _TriageResultForDedup  # type: ignore
        live_case_ids = {
            row.case_id
            for row in db.query(_TriageResultForDedup.case_id).all()
        }

        for event in existing_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            # Soft-deleted / hidden notables should not block a fresh paste.
            if payload.get("hidden_from_recent"):
                continue

            fields = payload.get("raw_fields") or payload.get("fields", {}) or {}
            key = build_notable_dedup_key(fields, payload.get("sanitized_text", ""))
            if key and key not in existing_by_key:
                existing_by_key[key] = {
                    "event": event,
                    "payload": payload,
                    "fields": fields,
                    "artifact_paths": payload.get("artifact_paths") or {},
                    "promoted_case_id": payload.get("promoted_case_id"),
                }

        for index, segment_text in enumerate(segments):
            parsed_fields = parse_structured_notable(segment_text)
            if not parsed_fields:
                parsed_fields = parse_pasted_notable(segment_text)

            # Apply small normalization tweaks (e.g., Destination from
            # Destination NT Hostname) before rendering/sanitizing.
            parsed_fields = normalize_notable_fields(parsed_fields)
            description_text = extract_notable_section(
                segment_text,
                "Description",
                [
                    "event details",
                    "correlation search",
                    "history",
                    "related investigations",
                    "drill-down search",
                    "adaptive responses",
                ],
            )
            if description_text and not parsed_fields.get("description"):
                parsed_fields["description"] = description_text
            parsed_fields = infer_notable_fields_from_description(parsed_fields)
            raw_query_fields = normalize_notable_fields(parsed_fields.copy())

            base_structured_text = render_notable_fields(parsed_fields) or segment_text
            history_text = extract_notable_history(segment_text)
            if history_text:
                structured_text = f"{base_structured_text}\n\nHistory\n{history_text}"
            else:
                structured_text = base_structured_text

            if request.redaction_enabled:
                sanitized_text, mapping = sanitize_logs_with_tokens(structured_text)
                sanitized_text = sanitize_pii_phi(sanitized_text)
                sanitized_fields = parse_structured_notable(sanitized_text)
                parsed_sanitized_fallback = parse_pasted_notable(sanitized_text)
                for key, value in parsed_sanitized_fallback.items():
                    if value and key not in sanitized_fields:
                        sanitized_fields[key] = value
                # preserve original time / rule_id if we parsed them before masking
                if parsed_fields.get("time"):
                    sanitized_fields["time"] = parsed_fields["time"]
                if parsed_fields.get("rule_id") and not sanitized_fields.get("rule_id"):
                    sanitized_fields["rule_id"] = parsed_fields["rule_id"]
                sanitized_fields = normalize_notable_fields(sanitized_fields)
            else:
                # no masking: keep parsed fields and structured text as-is
                sanitized_text = structured_text
                mapping = {}
                sanitized_fields = normalize_notable_fields(parsed_fields.copy())

            sanitized_history = extract_notable_history(sanitized_text)
            if not sanitized_history:
                sanitized_history = (sanitized_fields.get("closure_summary") or "").strip()
            parse_assessment = build_parse_assessment(raw_query_fields, sanitized_text, sanitized_history)

            # Dedup for both open and historical pastes.
            artifact_paths = None
            event = None
            dedup_key = build_notable_dedup_key(sanitized_fields, sanitized_text)
            # Also try raw (pre-redaction) fields so rule_id is never lost to tokens
            if not dedup_key:
                dedup_key = build_notable_dedup_key(raw_query_fields, sanitized_text)
            existing = existing_by_key.get(dedup_key) if dedup_key else None
            was_deduplicated = False
            skip_reason = None

            if existing:
                promoted_case_id = existing.get("promoted_case_id")
                # If the prior paste was promoted but that triage case was deleted,
                # treat this as free to re-add (update the existing event in place).
                orphaned_promotion = bool(
                    promoted_case_id and promoted_case_id not in live_case_ids
                )
                if not orphaned_promotion:
                    event = existing["event"]
                    artifact_paths = existing.get("artifact_paths") or {}
                    was_deduplicated = True
                    skipped_count += 1
                    seg_num = index + 1
                    existing_id = event.id if event else None
                    key_preview = (dedup_key or "")[:48]
                    skip_reason = f"already exists as event {existing_id} ({key_preview})"
                    skipped_segments.append(f"#{seg_num}")
                else:
                    # Reclaim the orphaned event: fall through to update path below
                    # by removing it from the dedup map and deleting the stale row.
                    stale_event = existing.get("event")
                    if stale_event is not None:
                        try:
                            db.delete(stale_event)
                            db.flush()
                        except Exception:
                            pass
                    if dedup_key in existing_by_key:
                        del existing_by_key[dedup_key]
                    existing = None

            if existing:
                pass  # already handled as skip above
            else:
                artifact_paths = save_notable_artifacts(
                    deps.get_platform_root(), sanitized_text, mapping, sanitized_fields
                )
                payload = {
                    "record_type": "splunk_notable_paste",
                    "raw_fields": raw_query_fields,
                    "fields": sanitized_fields,
                    "sanitized_text": sanitized_text,
                    "history": sanitized_history,
                    "parse_assessment": parse_assessment,
                    "saved_at": utcnow_naive().isoformat(),
                    "artifact_paths": artifact_paths,
                    "historical": request.historical,
                    "segment_index": index,
                    "segment_count": len(segments),
                    "dedup_key": dedup_key,
                }

                source = (
                    sanitized_fields.get("correlation_search")
                    or sanitized_fields.get("title")
                    or sanitized_fields.get("rule_name")
                    or "Pasted Splunk notable"
                )
                host = (
                    sanitized_fields.get("host")
                    or sanitized_fields.get("destination")
                    or "unknown"
                )
                timestamp = parse_notable_timestamp(parsed_fields.get("time", ""))

                event = SplunkEvent(
                    sourcetype="splunk:notable:pasted",
                    source=source,
                    host=host,
                    raw=json.dumps(payload),
                    timestamp=timestamp,
                )
                db.add(event)
                pending_events.append(event)
                added_count += 1

                # Cache so later segments in the same paste also dedup.
                # event.id is assigned on flush/commit; use a placeholder
                # until the single batch commit below.
                if dedup_key:
                    existing_by_key[dedup_key] = {
                        "event": event,
                        "payload": payload,
                        "fields": sanitized_fields,
                        "artifact_paths": artifact_paths,
                    }

            events_info.append({
                "event_id": None,  # filled after batch commit
                "event_ref": event,
                "raw_fields": raw_query_fields,
                "parsed_fields": sanitized_fields,
                "parse_assessment": parse_assessment,
                "artifact_paths": artifact_paths or {},
                "mapping_entries": len(mapping),
                "deduplicated": was_deduplicated,
                "skip_reason": skip_reason,
                "segment_index": index + 1,
            })
            total_mapping_entries += len(mapping)

        # Single commit for the whole bulk paste (major speed win vs per-row commits)
        if pending_events:
            db.commit()
            for event in pending_events:
                try:
                    db.refresh(event)
                except Exception:
                    pass

            # C4: record THIS paste's inserts on the boundary manifest exactly
            # as ingest_manifest does (ingest stats + ingest_window +
            # inserted_ids), so purge_batch can undo the paste batch.
            paste_ingest = splunk_boundary.record_paste_ingest(
                paste_manifest,
                rows_read=len(segments),
                rows_inserted=added_count,
                rows_skipped=skipped_count,
                inserted_ids=[e.id for e in pending_events],
            )

        for info in events_info:
            ref = info.pop("event_ref", None)
            if ref is not None:
                info["event_id"] = getattr(ref, "id", None)

        first_event = events_info[0] if events_info else {}

        # Human-readable summary, e.g.
        # "Added 4 notables. Skipped #2 and #5 (already in database)."
        if skipped_count and added_count:
            message = (
                f"Added {added_count} notable{'s' if added_count != 1 else ''}. "
                f"Skipped {', '.join(skipped_segments)} "
                f"(already in database)."
            )
        elif skipped_count and not added_count:
            message = (
                f"No new notables added. Skipped {', '.join(skipped_segments)} "
                f"(already in database)."
            )
        elif added_count:
            message = f"Added {added_count} notable{'s' if added_count != 1 else ''}."
        else:
            message = "No notables processed."

        return {
            "success": True,
            "segment_count": len(segments),
            "added": added_count,
            "skipped": skipped_count,
            "message": message,
            "events": events_info,
            "batch_id": paste_manifest["batch_id"],
            # Backwards-compatible single-event fields (use first segment)
            "event_id": first_event.get("event_id"),
            "raw_fields": first_event.get("raw_fields"),
            "parsed_fields": first_event.get("parsed_fields"),
            "parse_assessment": first_event.get("parse_assessment"),
            "artifact_paths": first_event.get("artifact_paths") or {},
            "mapping_entries": total_mapping_entries,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/api/db/notables", tags=["Database"])
def list_recent_notables(
    limit: int = Query(20, ge=1, le=200),
    delete_event_id: Optional[int] = Query(None, description="If provided, delete this pasted notable before listing"),
):
    """List recently pasted sanitized Splunk notables (optionally deleting one first)."""
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()

        # Optional delete/hide step using the same session. Historical
        # notables or those backing an existing triage case are retained
        # and hidden from the recent list instead of being deleted.
        if delete_event_id is not None:
            event = db.query(SplunkEvent).filter(
                SplunkEvent.id == delete_event_id,
                SplunkEvent.sourcetype == "splunk:notable:pasted",
            ).first()
            if event:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    payload = {}

                is_historical = bool(payload.get("historical"))

                promoted_case_id = payload.get("promoted_case_id")
                has_triage_case = False
                try:
                    if promoted_case_id:
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                        has_triage_case = existing_case is not None
                    else:
                        canonical_case_id = f"NOTABLE-{event.id}"
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                        has_triage_case = existing_case is not None
                except Exception:
                    has_triage_case = False

                if is_historical or has_triage_case:
                    payload["hidden_from_recent"] = True
                    event.raw = json.dumps(payload)
                else:
                    db.delete(event)
                db.commit()
        rows = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).limit(limit).all()

        serialized = [serialize_recent_notable(row) for row in rows]
        visible = [n for n in serialized if not n.get("hidden_from_recent")]
        return visible
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/api/db/notables/historical", tags=["Database"])
def list_historical_notables(
    limit: int = Query(20, ge=1, le=500),
):
    """List a high-level summary of historical (closed) pasted notables.

    Returns a compact view suitable for quick baseline reference without
    flooding the main triage grid.
    """
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        rows = (
            db.query(SplunkEvent)
            .filter(SplunkEvent.sourcetype == "splunk:notable:pasted")
            .order_by(SplunkEvent.ingested_at.desc())
            .limit(limit)
            .all()
        )

        summaries = []
        for event in rows:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            if not payload.get("historical"):
                continue

            fields = payload.get("fields", {}) or {}
            history_text = str(payload.get("history") or "").strip()
            history_summary = ""
            if history_text:
                first_line = next((line.strip() for line in history_text.splitlines() if line.strip()), "")
                history_summary = first_line[:157].rstrip() + "..." if len(first_line) > 160 else first_line
            summaries.append({
                "id": event.id,
                "title": fields.get("title") or fields.get("correlation_search") or event.source,
                "correlation_search": fields.get("correlation_search"),
                "host": fields.get("host") or event.host,
                "user": fields.get("user") or fields.get("username"),
                "urgency": fields.get("urgency"),
                "disposition": fields.get("disposition"),
                "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
                "history_summary": history_summary,
            })

        return summaries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.post("/api/db/notables/backfill-closure-notes", tags=["Database"])
def backfill_historical_closure_notes():
    """Create concise closure summaries for historical notables missing one.

    Historical pasted notables predate the structured closure-note workflow, so
    this backfill stores the summary in the notable payload and also records a
    ClosureNote row using a stable synthetic historical case id.
    """
    db = None
    try:
        from db.models import ClosureNote, SessionLocal, SplunkEvent

        db = SessionLocal()
        rows = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.id.asc()).all()

        generated = []
        skipped = []
        now = utcnow_naive()
        for event in rows:
            try:
                payload = json.loads(event.raw or "{}")
            except Exception:
                continue
            if not payload.get("historical"):
                continue

            existing_history = str(payload.get("history") or "").strip()
            synthetic_case_id = f"HISTORICAL-NOTABLE-{event.id}"
            if existing_history:
                skipped.append(event.id)
                continue

            fields = payload.get("fields") or {}
            title = (fields.get("title") or fields.get("correlation_search") or event.source or "Security notable").strip()
            host = (fields.get("host") or fields.get("destination") or event.host or "unknown host").strip()
            user = (fields.get("user") or fields.get("username") or "unknown user").strip()
            process = (fields.get("process") or fields.get("process_name") or fields.get("parent_process") or "the recorded process").strip()
            disposition = (fields.get("disposition") or "historical disposition not specified").strip()
            summary = (
                f"Closed historical notable '{title}' was recorded on {host} for {user}; "
                f"the triggering activity was associated with {process}."
                f" Recorded disposition: {disposition}."
            )

            payload["history"] = summary
            payload["closure_summary"] = summary
            event.raw = json.dumps(payload)

            rule_id = (fields.get("rule_id") or fields.get("correlation_search") or fields.get("rule_name") or "historical_notable").strip()
            note = db.query(ClosureNote).filter(ClosureNote.case_id == synthetic_case_id).first()
            if not note:
                note = ClosureNote(
                    case_id=synthetic_case_id,
                    rule_id=rule_id,
                    analyst_notes="Backfilled from historical notable metadata for display and baseline analysis.",
                    generated_note=summary,
                    status="closed",
                    created_at=now,
                    submitted_at=now,
                )
                db.add(note)
            generated.append(event.id)

        db.commit()
        return {"generated": len(generated), "event_ids": generated, "skipped": len(skipped)}
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/api/db/notables/{event_id}", tags=["Database"])
def get_pasted_notable_details(event_id: int):
    """Return full parsed and sanitized details for a closed pasted notable."""
    try:
        from db.models import SessionLocal, SplunkEvent
        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted",
        ).first()
        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")
        payload = json.loads(event.raw or "{}")
        return {
            "id": event.id,
            "historical": bool(payload.get("historical")),
            "fields": payload.get("fields") or {},
            "raw_fields": payload.get("raw_fields") or {},
            "sanitized_text": payload.get("sanitized_text") or "",
            "history": payload.get("history") or "",
            "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.delete("/api/db/notables/{event_id}", tags=["Database"])
def delete_pasted_notable(event_id: int):
    """Delete a pasted Splunk notable from the database.

    This removes the stored sanitized text and metadata for the pasted notable
    but does not delete any triage cases that may have been created from it.
    """
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted",
        ).first()

        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")

        # For historical (closed) pasted notables, or events that already
        # back an existing triage case, retain the underlying record in the
        # database and simply hide it from the recent list so stats and
        # triage-source lookups remain valid.
        try:
            payload = json.loads(event.raw) if event.raw else {}
        except Exception:
            payload = {}

        is_historical = bool(payload.get("historical"))

        promoted_case_id = payload.get("promoted_case_id")
        has_triage_case = False
        try:
            if promoted_case_id:
                existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                has_triage_case = existing_case is not None
            else:
                # Fallback to the canonical NOTABLE-<id> pattern used when
                # promoting notables into triage.
                canonical_case_id = f"NOTABLE-{event.id}"
                existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                has_triage_case = existing_case is not None
        except Exception:
            # If the triage lookup fails for any reason, fall back to the
            # historical flag alone to decide whether to delete.
            has_triage_case = False

        if has_triage_case and not is_historical:
            payload["hidden_from_recent"] = True
            event.raw = json.dumps(payload)
        else:
            db.delete(event)

        db.commit()

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.post("/api/db/notables/{event_id}/delete", tags=["Database"])
def delete_pasted_notable_post(event_id: int):
    """Compatibility endpoint to delete a pasted notable via POST.

    Some environments or proxies may not allow DELETE from the browser UI,
    so the frontend can call this POST variant instead. Logic is delegated
    to the main delete_pasted_notable handler above.
    """
    return delete_pasted_notable(event_id)


@router.post("/api/db/notables/batch-delete", tags=["Database"])
def batch_delete_pasted_notables(payload: Dict[str, Any]):
    """Delete multiple pasted notables in one call.

    Expects JSON payload:
    {"event_ids": [1, 2, 3, ...]}
    """
    try:
        event_ids = payload.get("event_ids") or []
        if not isinstance(event_ids, list) or not event_ids:
            raise HTTPException(status_code=400, detail="event_ids list is required")

        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()
        deleted: List[int] = []
        missing: List[int] = []

        try:
            for raw_id in event_ids:
                try:
                    eid = int(raw_id)
                except Exception:
                    continue
                event = db.query(SplunkEvent).filter(
                    SplunkEvent.id == eid,
                    SplunkEvent.sourcetype == "splunk:notable:pasted",
                ).first()
                if not event:
                    missing.append(eid)
                    continue

                try:
                    payload_raw = json.loads(event.raw) if event.raw else {}
                except Exception:
                    payload_raw = {}

                is_historical = bool(payload_raw.get("historical"))

                promoted_case_id = payload_raw.get("promoted_case_id")
                has_triage_case = False
                try:
                    if promoted_case_id:
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                        has_triage_case = existing_case is not None
                    else:
                        canonical_case_id = f"NOTABLE-{event.id}"
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                        has_triage_case = existing_case is not None
                except Exception:
                    has_triage_case = False

                if has_triage_case and not is_historical:
                    payload_raw["hidden_from_recent"] = True
                    event.raw = json.dumps(payload_raw)
                else:
                    db.delete(event)

                deleted.append(eid)

            db.commit()
        finally:
            db.close()

        return {"success": True, "deleted": deleted, "missing": missing}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/db/triage/{case_id}/notable", tags=["Database"])
def get_triage_source_notable(case_id: str):
    """Return the source pasted notable details for a promoted triage case."""
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal
        from services.evidence_service import resolve_source_notable_for_case

        db = SessionLocal()
        try:
            return resolve_source_notable_for_case(db, case_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

