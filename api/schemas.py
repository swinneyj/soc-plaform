"""
SOC Platform API schemas — single source of truth for payload/response models.

This was Piece A of backend modularization (see
docs/BACKEND_MODULARIZATION_PLAN.md), previously a zero-reference duplicate;
it is now the live home for every Pydantic model shared across the API
surface. api.routes.* modules import from here (api.main re-exports a few
names for backward compatibility). No behavior change — field definitions
are verbatim from their pre-move homes.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Jobs / tools
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    """Lifecycle of an async tool/job run (shared by tools + search-one)."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolRequest(BaseModel):
    tool_name: str = Field(..., description="Name of the tool to execute")
    arguments: Optional[Dict[str, str]] = Field(default={}, description="Tool CLI arguments")
    silent: bool = Field(default=False, description="Suppress stdout output")


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    tool_name: str
    created_at: str
    completed_at: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    arguments: Optional[Dict[str, Any]] = None
    artifacts: List[str] = Field(default_factory=list)


class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    case_id: str
    model: str = ""  # empty = auto-resolve an installed model at call time
    context: str = ""
    prior_analysis: str = ""
    analysis_stage: str = "initial"
    analysis_phase: int = 1


# ---------------------------------------------------------------------------
# Splunk one-click search
# ---------------------------------------------------------------------------

class SplunkSearchOnePayload(BaseModel):
    """One-click read-only search execution (Phase 3, mock-first).

    Either ``spl`` (a raw template, e.g. an editable Phase 2 card) or a
    ``query_title`` matching one of the rule's stored supportive queries.
    Placeholder substitution happens server-side from the case's notable
    fields so the run is reproducible and auditable.
    """

    case_id: str = Field(..., description="Triage case whose notable fields ground the query")
    query_title: str = Field(..., description="Title of the supportive query to run (also the evidence ledger key)")
    spl: Optional[str] = Field(None, description="Optional raw SPL template override; falls back to the rule's stored query")
    earliest: str = Field("-7d", description="SPL earliest time bound")
    latest: str = Field("now", description="SPL latest time bound")
    finding_type: Optional[str] = Field("neutral", description="Advisory analyst label; the AI assessment decides the evidence direction")
    question_resolution: Optional[str] = Field("not_resolved", description="Advisory analyst label; targeted inquiries resolve from substantive evidence")
    target_questions: List[str] = Field(default_factory=list, description="Open inquiries this run targets")


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class InvestigationEvidenceEntryPayload(BaseModel):
    query_title: str = Field(..., description="Short title for the investigative query or evidence item")
    query_text: Optional[str] = Field("", description="SPL or other query text used to gather the evidence")
    result_text: Optional[str] = Field("", description="Key rows, findings, or summary pasted by the analyst")
    analyst_summary: Optional[str] = Field("", description="Analyst takeaway or interpretation of the evidence")
    finding_type: Optional[str] = Field("neutral", description="Advisory analyst label, stored for the audit trail only — the AI's per-card assessment decides the evidence direction")
    question_resolution: Optional[str] = Field("not_resolved", description="Advisory analyst label, stored for the audit trail only — targeted inquiries resolve when substantive evidence answers them")
    target_questions: List[str] = Field(default_factory=list, description="Open inquiries targeted by this evidence")
    result_status: Optional[str] = Field("success", description="Execution status: success, no_results, data_source_unavailable, query_failed, not_run, benign_result")
    collection_time: Optional[str] = Field(None, description="ISO timestamp when evidence was collected")
    source_system: Optional[str] = Field("splunk", description="Telemetry source system (splunk, mde, defender, edr, firewall, etc.)")


class InvestigationEvidenceBatchPayload(BaseModel):
    entries: List[InvestigationEvidenceEntryPayload] = Field(default_factory=list)
    source_system: str = Field("phase2_manual", description="Source or stage label for this evidence batch")
    replace_existing: bool = Field(True, description="Replace existing evidence for this case and source_system before saving")


# ---------------------------------------------------------------------------
# Supportive queries + placeholder aliases
# ---------------------------------------------------------------------------

class SupportiveQueryPayload(BaseModel):
    """Payload for creating/updating supportive SPL queries.

    This is intentionally minimal so analysts can tune queries on the fly
    without touching the underlying correlation rule definition.
    """

    rule_id: str = Field(..., description="Logical correlation rule identifier")
    title: str = Field(..., description="Short name for this supportive query")
    description: Optional[str] = Field("", description="What this query is used for")
    spl_query: str = Field(..., description="SPL to run in Splunk or another system")


class SupportivePlaybookDraftRequest(BaseModel):
    case_id: str = Field(..., description="Case used to ground the draft playbook")


class SupportiveResultsImportRequest(BaseModel):
    case_id: str = Field(..., description="Case used to ground the draft playbook")
    content: str = Field(..., min_length=1, max_length=2_000_000, description="Pasted or uploaded Splunk results")
    filename: Optional[str] = Field(None, description="Original filename, if uploaded")


class SupportiveQueryUpdatePayload(BaseModel):
    """Partial update payload for supportive SPL queries."""

    rule_id: Optional[str] = Field(None, description="Logical correlation rule identifier")
    title: Optional[str] = Field(None, description="Short name for this supportive query")
    description: Optional[str] = Field(None, description="What this query is used for")
    spl_query: Optional[str] = Field(None, description="SPL to run in Splunk or another system")


class PlaceholderAliasPayload(BaseModel):
    """Payload for creating/updating placeholder aliases.

    Aliases let analysts define logical names (e.g., "host", "dest",
    "user") that map to one or more notable fields without touching
    code. These are consumed by the frontend when rendering supportive
    queries with $placeholder$ tokens.
    """

    alias: str = Field(..., description="Logical placeholder name (e.g., host, dest, user)")
    fields: List[str] = Field(..., description="Candidate field names to resolve values from")
    description: Optional[str] = Field("", description="Human-readable description of this alias")


class PlaceholderAliasUpdatePayload(BaseModel):
    """Partial update payload for placeholder aliases."""

    alias: Optional[str] = Field(None, description="Logical placeholder name (e.g., host, dest, user)")
    fields: Optional[List[str]] = Field(None, description="Candidate field names to resolve values from")
    description: Optional[str] = Field(None, description="Human-readable description of this alias")


# ---------------------------------------------------------------------------
# Pasted notables
# ---------------------------------------------------------------------------

class PastedNotableRequest(BaseModel):
    raw_text: str = Field(..., description="Pasted notable text from Splunk Incident Review")
    redaction_enabled: bool = Field(
        True,
        description="Whether to apply tokenizer-style redaction to the pasted notable",
    )
    historical: bool = Field(
        False,
        description="Whether this pasted notable represents a closed/historical case",
    )


class NotableFetchSplRequest(BaseModel):
    """Optional filters used to build a clean notable-fetch SPL query.

    Provide whatever you know (rule name / search_name, host/dest, time
    window). The returned SPL is meant to be run in Splunk, then the
    Statistics table row(s) copied back into the paste box as Label: value
    lines — far more reliable than copying the Incident Review detail pane
    (which glues UI badges into field values).

    Primary path uses the notable index (works when `incident_review` is
    empty for the analyst role). A secondary incident_review variant is
    also returned for environments where that macro is available.
    """

    correlation_search: Optional[str] = Field(
        None, description="ES correlation search / search_name / rule title"
    )
    rule_name: Optional[str] = Field(None, description="Notable rule_name / title")
    dest: Optional[str] = Field(None, description="Destination / host (supports trailing * wildcard)")
    host: Optional[str] = Field(None, description="Host field if different from dest")
    user: Optional[str] = Field(None, description="User / account name")
    event_id: Optional[str] = Field(None, description="Splunk/ES event_id if known")
    rule_id: Optional[str] = Field(None, description="ES rule_id (...@@notable@@...) if known")
    earliest: str = Field("-7d", description="SPL earliest (e.g. -24h, -7d, 09/10/2026:00:00:00)")
    latest: str = Field("now", description="SPL latest")
    max_rows: int = Field(20, ge=1, le=200, description="head N rows")
    notable_index: str = Field(
        "notable",
        description="Index that holds notable events (default: notable). Some sites use risk or a custom index.",
    )
