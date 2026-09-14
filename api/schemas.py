"""
SOC Platform API schemas — Piece A of backend modularization.

Extracted from api/main.py (5314 lines) as the first additive step.
See docs/BACKEND_MODULARIZATION_PLAN.md — Piece A scaffolding.

Goal: single source of truth for Pydantic models. api/main.py will
import from here (and re-export for backward compat) in Piece B.
No behavior change — this file is additive only.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
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


class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None


class AnalyzeRequest(BaseModel):
    case_id: str
    model: str = "llama3.1:8b"
    context: str = ""
    prior_analysis: str = ""
    analysis_stage: str = "initial"


class InvestigationEvidenceEntryPayload(BaseModel):
    query_title: str = Field(..., description="Short title for the investigative query or evidence item")
    query_text: Optional[str] = Field("", description="SPL or other query text used to gather the evidence")
    result_text: Optional[str] = Field("", description="Key rows, findings, or summary pasted by the analyst")
    analyst_summary: Optional[str] = Field("", description="Analyst takeaway or interpretation of the evidence")
    finding_type: Optional[str] = Field(
        "neutral", description="Whether the evidence supports, refutes, or is neutral to the active hypothesis"
    )
    question_resolution: Optional[str] = Field(
        "not_resolved",
        description="Whether this evidence does not resolve, partially resolves, or resolves a targeted inquiry",
    )
    target_questions: List[str] = Field(default_factory=list, description="Open inquiries targeted by this evidence")
    result_status: Optional[str] = Field(
        "success",
        description="Execution status: success, no_results, data_source_unavailable, query_failed, not_run, benign_result",
    )
    collection_time: Optional[str] = Field(None, description="ISO timestamp when evidence was collected")
    source_system: Optional[str] = Field(
        "splunk", description="Telemetry source system (splunk, mde, defender, edr, firewall, etc.)"
    )


class InvestigationEvidenceBatchPayload(BaseModel):
    entries: List[InvestigationEvidenceEntryPayload] = Field(default_factory=list)
    source_system: str = Field("phase2_manual", description="Source or stage label for this evidence batch")
    replace_existing: bool = Field(
        True, description="Replace existing evidence for this case and source_system before saving"
    )


class SupportiveQueryPayload(BaseModel):
    """Payload for creating/updating supportive SPL queries."""

    rule_id: str = Field(..., description="Logical correlation rule identifier")
    title: str = Field(..., description="Short name for this supportive query")
    description: Optional[str] = Field("", description="What this query is used for")
    spl_query: str = Field(..., description="SPL to run in Splunk or another system")


class SupportiveQueryUpdatePayload(BaseModel):
    """Partial update payload for supportive SPL queries."""

    rule_id: Optional[str] = Field(None, description="Logical correlation rule identifier")
    title: Optional[str] = Field(None, description="Short name for this supportive query")
    description: Optional[str] = Field(None, description="What this query is used for")
    spl_query: Optional[str] = Field(None, description="SPL to run in Splunk or another system")


class PlaceholderAliasPayload(BaseModel):
    """Payload for creating/updating placeholder aliases."""

    alias: str = Field(..., description="Logical placeholder name (e.g., host, dest, user)")
    fields: List[str] = Field(..., description="Candidate field names to resolve values from")
    description: Optional[str] = Field("", description="Human-readable description of this alias")


class PlaceholderAliasUpdatePayload(BaseModel):
    """Partial update payload for placeholder aliases."""

    alias: Optional[str] = Field(None, description="Logical placeholder name (e.g., host, dest, user)")
    fields: Optional[List[str]] = Field(None, description="Candidate field names to resolve values from")
    description: Optional[str] = Field(None, description="Human-readable description of this alias")


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
    """Optional filters used to build a clean notable-fetch SPL query."""

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
