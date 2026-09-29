"""Rules surface: placeholder aliases, supportive playbook queries, and the
ES correlation-rule catalog.

Owns the JSON-file rebuild helpers (placeholder_aliases.json,
supportive_rules.json) that keep the file seeds in sync with the DB rows."""
import csv
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core_lib.utils import get_platform_root

logger = logging.getLogger("soc.api")

router = APIRouter()

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



def _rebuild_placeholder_aliases_file() -> None:
    """Persist current placeholder aliases into placeholder_aliases.json.

    This mirrors the DB state into a repo-backed JSON file so alias
    definitions can survive DB restores when sync_shared_logic_to_db.ps1
    re-imports shared logic.
    """
    try:
        from db.models import SessionLocal, PlaceholderAlias  # type: ignore

        db = SessionLocal()
        try:
            rows = (
                db.query(PlaceholderAlias)
                .order_by(PlaceholderAlias.alias.asc())
                .all()
            )
            aliases: List[Dict[str, Any]] = []
            for row in rows:
                try:
                    fields = json.loads(row.fields) if row.fields else []
                except Exception:
                    fields = []
                aliases.append(
                    {
                        "alias": (row.alias or "").strip(),
                        "fields": fields,
                        "description": row.description or "",
                    }
                )

            payload = {"aliases": aliases}
            output_path = os.path.join(get_platform_root(), "placeholder_aliases.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[placeholder_aliases.json sync] Failed to rebuild file: %s", e)



def _rebuild_supportive_rules_file() -> None:
    """Persist current supportive queries from DB into supportive_rules.json.

    This keeps the repo-backed supportive_rules.json in sync with DB edits
    made via the API/UI so that a later DB restore followed by
    sync_shared_logic_to_db.ps1 can automatically reapply supportive
    queries without manual JSON editing.
    """
    try:
        from db.models import SessionLocal, SupportiveQuery  # type: ignore

        db = SessionLocal()
        try:
            rows = (
                db.query(SupportiveQuery)
                .order_by(SupportiveQuery.rule_id.asc(), SupportiveQuery.title.asc())
                .all()
            )

            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row.rule_id, []).append(
                    {
                        "title": row.title,
                        "description": row.description or "",
                        "spl_query": row.spl_query,
                    }
                )

            payload = {
                "rules": [
                    {"rule_id": rule_id, "supportive_queries": queries}
                    for rule_id, queries in grouped.items()
                ]
            }

            output_path = os.path.join(get_platform_root(), "supportive_rules.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        finally:
            db.close()
    except Exception as e:  # best-effort only; never break API on failure
        logger.warning("[supportive_rules.json sync] Failed to rebuild file: %s", e)

# Helper Functions

@router.get("/api/db/placeholder-aliases", tags=["Rules"])
def list_placeholder_aliases():
    # List all defined placeholder aliases.
    # Response shape matches what the frontend expects:
    # [{"id", "alias", "fields", "description"}, ...]
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        rows = db.query(PlaceholderAlias).order_by(PlaceholderAlias.alias.asc()).all()
        db.close()

        aliases: List[Dict[str, Any]] = []
        for row in rows:
            try:
                fields = json.loads(row.fields) if row.fields else []
            except Exception:
                fields = []

            aliases.append({
                "id": row.id,
                "alias": (row.alias or "").strip(),
                "fields": fields,
                "description": row.description or "",
            })

        return aliases
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/db/placeholder-aliases/suggestions", tags=["Rules"])
def suggest_placeholder_alias_fields(
    limit_events: int = Query(50, ge=1, le=500, description="Number of recent pasted notables to scan"),
):
    """Suggest candidate field names for placeholder aliases from recent pasted notables."""

    # Scans recent SplunkEvent rows with sourcetype="splunk:notable:pasted", extracts the
    # "fields" dict from each event's raw JSON payload, and returns a frequency-ranked
    # list of field names observed. This backs the UI's "Suggest from recent notables"
    # button in the alias editor.
    #
    # Response shape:
    #     {"candidates": [{"field": "host", "count": N}, ...]}
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        try:
            rows = (
                db.query(SplunkEvent)
                .filter(SplunkEvent.sourcetype == "splunk:notable:pasted")
                .order_by(SplunkEvent.ingested_at.desc())
                .limit(limit_events)
                .all()
            )
        finally:
            db.close()

        field_counts: Dict[str, int] = {}
        for row in rows:
            try:
                payload = json.loads(row.raw) if row.raw else {}
            except Exception:
                continue

            fields = payload.get("fields") or {}
            if not isinstance(fields, dict):
                continue

            for name in fields.keys():
                if not name:
                    continue
                key = str(name).strip()
                if not key:
                    continue
                field_counts[key] = field_counts.get(key, 0) + 1

        candidates = [
            {"field": name, "count": count}
            for name, count in sorted(field_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        return {"candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/db/placeholder-aliases", tags=["Rules"])
def create_placeholder_alias(payload: PlaceholderAliasPayload):
    # Create a new placeholder alias.
    # Alias names are normalized to lowercase and must be unique.
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        alias_normalized = payload.alias.strip().lower()
        if not alias_normalized:
            db.close()
            raise HTTPException(status_code=400, detail="Alias name cannot be empty")

        # Enforce uniqueness at the application level for clearer errors.
        existing = db.query(PlaceholderAlias).filter(
            func.lower(PlaceholderAlias.alias) == alias_normalized
        ).first()
        if existing:
            db.close()
            raise HTTPException(status_code=400, detail=f"Alias '{alias_normalized}' already exists")

        cleaned_fields = [f.strip() for f in (payload.fields or []) if f and f.strip()]
        record = PlaceholderAlias(
            alias=alias_normalized,
            fields=json.dumps(cleaned_fields),
            description=(payload.description or "").strip(),
        )

        db.add(record)
        db.commit()
        db.refresh(record)

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

        db.close()

        return {
            "id": record.id,
            "alias": record.alias,
            "fields": cleaned_fields,
            "description": record.description or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/db/placeholder-aliases/{alias_id}", tags=["Rules"])
def update_placeholder_alias(alias_id: int, payload: PlaceholderAliasUpdatePayload):
    # Update an existing placeholder alias.
    # Supports partial updates for alias, fields, and description.
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        record = db.query(PlaceholderAlias).filter(PlaceholderAlias.id == alias_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Placeholder alias {alias_id} not found")

        if payload.alias is not None:
            new_alias = payload.alias.strip().lower()
            if not new_alias:
                db.close()
                raise HTTPException(status_code=400, detail="Alias name cannot be empty")

            existing = db.query(PlaceholderAlias).filter(
                func.lower(PlaceholderAlias.alias) == new_alias,
                PlaceholderAlias.id != alias_id,
            ).first()
            if existing:
                db.close()
                raise HTTPException(status_code=400, detail=f"Alias '{new_alias}' already exists")
            record.alias = new_alias

        if payload.fields is not None:
            cleaned_fields = [f.strip() for f in payload.fields if f and f.strip()]
            record.fields = json.dumps(cleaned_fields)

        if payload.description is not None:
            record.description = payload.description.strip()

        db.commit()
        db.refresh(record)

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

        db.close()

        try:
            fields = json.loads(record.fields) if record.fields else []
        except Exception:
            fields = []

        return {
            "id": record.id,
            "alias": record.alias,
            "fields": fields,
            "description": record.description or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/db/placeholder-aliases/{alias_id}", tags=["Rules"])
def delete_placeholder_alias(alias_id: int):
    # Delete a placeholder alias definition.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        record = db.query(PlaceholderAlias).filter(PlaceholderAlias.id == alias_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Placeholder alias {alias_id} not found")

        db.delete(record)
        db.commit()

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

        db.close()

        return {"success": True, "deleted_id": alias_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/api/db/supportive-queries", tags=["Rules"])
def list_supportive_queries(rule_id: Optional[str] = Query(default=None, description="Filter by logical rule_id")):
    # List supportive SPL queries.
    # When a rule_id is provided, only queries for that rule are returned.
    # Otherwise, all supportive queries are listed. This API backs the
    # analyst-facing editor so supportive queries can be tuned on the fly
    # without touching JSON seed files.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        query = db.query(SupportiveQuery)
        if rule_id:
            query = query.filter(SupportiveQuery.rule_id == rule_id)
        rows = query.order_by(SupportiveQuery.rule_id.asc(), SupportiveQuery.title.asc()).all()
        db.close()

        return [
            {
                "id": r.id,
                "rule_id": r.rule_id,
                "title": r.title,
                "description": r.description,
                "spl_query": r.spl_query,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/db/supportive-queries/draft", tags=["Rules"])
def draft_supportive_queries(payload: SupportivePlaybookDraftRequest):
    """Create reviewable, unsaved SPL drafts for a rule without a playbook.

    Drafts intentionally use an explicit index placeholder. They are never
    returned as authoritative investigation cards until an analyst approves
    and saves them through the normal supportive-query editor.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQuery

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == payload.case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {payload.case_id} not found")

        rule_key = (case.rule_id or "").strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        existing = db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_key).count()
        if existing:
            return {"requires_approval": False, "playbook_available": True, "rule_id": case.rule_id, "draft_queries": []}

        source_fields: Dict[str, Any] = {}
        # SplunkEvent stores the promotion linkage inside the raw JSON
        # payload, rather than as a mapped ORM column.
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            if not event.raw:
                continue
            try:
                event_payload = json.loads(event.raw) or {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == payload.case_id:
                source_fields = event_payload.get("fields") or {}
                break

        host = source_fields.get("host") or source_fields.get("destination") or "$host$"
        user = source_fields.get("user") or source_fields.get("username") or "$user$"
        process = source_fields.get("process") or source_fields.get("process_name") or "$process$"
        rule_label = case.rule_name or source_fields.get("correlation_search") or case.rule_id or "unsupported rule"
        anchor = " ".join(str(source_fields.get(key) or "") for key in ("title", "description", "correlation_search", "rule_id", "process", "file_path"))
        quoted_anchor = re.sub(r"[^A-Za-z0-9_.:/ -]", " ", anchor).strip()[:180] or rule_label

        drafts = [
            {
                "rule_id": rule_key,
                "title": "Rule-scoped event context",
                "description": "Review events matching the notable's rule and core entities. Replace the index placeholder before approval.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" ("{quoted_anchor}" OR process="{process}") | table _time host user process parent_process command_line _raw | sort 0 -_time',
            },
            {
                "rule_id": rule_key,
                "title": "Related activity by host and user",
                "description": "Look for adjacent activity by the affected host and identity around the notable time window.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" user="{user}" earliest=-24h | stats count values(process) as processes values(parent_process) as parent_processes values(command_line) as command_lines by host user | sort -count',
            },
            {
                "rule_id": rule_key,
                "title": "Process and destination correlation",
                "description": "Check whether the process or destination appears with related network or execution activity.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" (process="{process}" OR dest="{source_fields.get("destination_ip") or "$destination_ip$"}") | table _time host user process parent_process dest dest_ip command_line action result | sort 0 -_time',
            },
        ]
        return {
            "requires_approval": True,
            "playbook_available": False,
            "case_id": payload.case_id,
            "rule_id": rule_key,
            "rule_name": rule_label,
            "draft_queries": drafts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@router.get("/api/db/supportive-queries/status/{case_id}", tags=["Rules"])
def supportive_playbook_status(case_id: str):
    """Report whether a case's rule already has an approved playbook."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQuery

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        source_rule_id = ""
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            if not event.raw:
                continue
            try:
                event_payload = json.loads(event.raw) or {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == case_id:
                source_rule_id = ((event_payload.get("raw_fields") or {}).get("rule_id") or
                                  (event_payload.get("fields") or {}).get("rule_id") or "").strip()
                break

        rule_key = (case.rule_id or source_rule_id).strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        query_count = db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_key).count()
        return {
            "case_id": case_id,
            "rule_id": rule_key,
            "rule_name": case.rule_name,
            "playbook_available": query_count > 0,
            "query_count": query_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@router.post("/api/db/supportive-queries/import-results", tags=["Rules"])
def import_supportive_results(payload: SupportiveResultsImportRequest):
    """Turn analyst-provided Splunk results into reviewable, unsaved SPL drafts."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == payload.case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {payload.case_id} not found")

        records: List[Dict[str, Any]] = []
        text_content = payload.content.strip()
        suffix = (payload.filename or "").lower()
        try:
            parsed = json.loads(text_content) if suffix.endswith(".json") or text_content[:1] in "[{" else None
            if isinstance(parsed, list):
                records = [item for item in parsed if isinstance(item, dict)]
            elif isinstance(parsed, dict):
                for key in ("results", "events", "data", "rows"):
                    if isinstance(parsed.get(key), list):
                        records = [item for item in parsed[key] if isinstance(item, dict)]
                        break
                if not records:
                    records = [parsed]
        except Exception:
            records = []

        if not records and (suffix.endswith(".csv") or any("," in line for line in text_content.splitlines()[:3])):
            try:
                reader = csv.DictReader(io.StringIO(text_content))
                records = [dict(row) for row in reader if row]
            except Exception:
                records = []

        observed: Dict[str, List[str]] = {"index": [], "sourcetype": [], "host": [], "field_names": []}
        def add_observed(bucket: str, value: Any):
            if value is None:
                return
            for item in (value if isinstance(value, list) else [value]):
                value_text = str(item).strip().strip('"')
                if value_text and value_text not in observed[bucket]:
                    observed[bucket].append(value_text)

        for record in records:
            lowered = {str(k).lower(): v for k, v in record.items()}
            add_observed("index", lowered.get("index") or lowered.get("indexes") or lowered.get("search_index"))
            add_observed("sourcetype", lowered.get("sourcetype") or lowered.get("source_type") or lowered.get("searchtype"))
            add_observed("host", lowered.get("host") or lowered.get("dest") or lowered.get("destination"))
            observed["field_names"].extend(str(k) for k in record.keys() if str(k) not in observed["field_names"])

        for line in text_content.splitlines():
            for key, bucket in (("index", "index"), ("sourcetype", "sourcetype"), ("searchtype", "sourcetype"), ("host", "host")):
                match = re.search(rf"(?:^|[\s,]){key}\s*[:=]\s*[\"']?([^\s,\"']+)", line, re.IGNORECASE)
                if match:
                    add_observed(bucket, match.group(1))

        # Pull the case's core entities from the stored notable.
        source_fields: Dict[str, Any] = {}
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            try:
                event_payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == payload.case_id:
                source_fields = event_payload.get("fields") or {}
                break
        host = source_fields.get("host") or source_fields.get("destination") or (observed["host"][0] if observed["host"] else "$host$")
        user = source_fields.get("user") or source_fields.get("username") or "$user$"
        process = source_fields.get("process") or source_fields.get("process_name") or "$process$"
        rule_key = (case.rule_id or source_fields.get("rule_id") or "").strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        index_clause = " OR ".join(f'index="{value}"' for value in observed["index"]) or "index=<REVIEW_REQUIRED>"
        sourcetype_clause = " OR ".join(f'sourcetype="{value}"' for value in observed["sourcetype"])
        source_filter = f'({index_clause})' if " OR " in index_clause else index_clause
        if sourcetype_clause:
            source_filter += f" ({sourcetype_clause})"
        drafts = [
            {"rule_id": rule_key, "title": "Observed event context", "description": "Search the observed Splunk data source for the notable's core entities.", "spl_query": f'{source_filter} host="{host}" (process="{process}" OR user="{user}") | table _time host user process parent_process command_line _raw | sort 0 -_time'},
            {"rule_id": rule_key, "title": "Related host and user activity", "description": "Review adjacent activity for the affected host and identity in the imported data source.", "spl_query": f'{source_filter} host="{host}" user="{user}" earliest=-24h | stats count values(process) as processes values(command_line) as command_lines by host user | sort -count'},
            {"rule_id": rule_key, "title": "Process and persistence correlation", "description": "Check for related execution or persistence activity around the notable.", "spl_query": f'{source_filter} host="{host}" (process="{process}" OR file_path="{source_fields.get("file_path") or "$file_path$"}") | table _time host user process parent_process file_path command_line action result | sort 0 -_time'},
        ]
        return {"requires_approval": True, "playbook_available": False, "case_id": payload.case_id, "rule_id": rule_key, "observed": observed, "record_count": len(records), "draft_queries": drafts}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@router.post("/api/db/supportive-queries", tags=["Rules"])
def create_supportive_query(payload: SupportiveQueryPayload):
    # Create a new supportive SPL query for a correlation rule.
    # IDs are assigned explicitly based on the current max(id) to avoid
    # depending on a potentially misaligned Postgres sequence, mirroring
    # the import logic used by the ES rules importer.
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        try:
            max_id = db.query(func.max(SupportiveQuery.id)).scalar() or 0
        except Exception:
            max_id = 0
        next_id = int(max_id) + 1

        record = SupportiveQuery(
            id=next_id,
            rule_id=payload.rule_id.strip(),
            title=payload.title.strip(),
            description=(payload.description or "").strip(),
            spl_query=payload.spl_query.strip(),
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

        db.close()

        return {
            "id": record.id,
            "rule_id": record.rule_id,
            "title": record.title,
            "description": record.description,
            "spl_query": record.spl_query,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/db/supportive-queries/{query_id}", tags=["Rules"])
def update_supportive_query(query_id: int, payload: SupportiveQueryUpdatePayload):
    # Update an existing supportive SPL query.
    # Supports partial updates; any field omitted from the payload is left
    # unchanged. Rule IDs can be adjusted if needed when re-grouping
    # queries under a different logical rule.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        record = db.query(SupportiveQuery).filter(SupportiveQuery.id == query_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Supportive query {query_id} not found")

        if payload.rule_id is not None:
            record.rule_id = payload.rule_id.strip()
        if payload.title is not None:
            record.title = payload.title.strip()
        if payload.description is not None:
            record.description = payload.description.strip()
        if payload.spl_query is not None:
            record.spl_query = payload.spl_query.strip()

        db.commit()
        db.refresh(record)

        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

        db.close()

        return {
            "id": record.id,
            "rule_id": record.rule_id,
            "title": record.title,
            "description": record.description,
            "spl_query": record.spl_query,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/db/supportive-queries/{query_id}", tags=["Rules"])
def delete_supportive_query(query_id: int):
    # Delete a supportive SPL query.
    # This does not touch stored supportive_query_results; those remain as
    # historical evidence even if the underlying query definition is
    # retired.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        record = db.query(SupportiveQuery).filter(SupportiveQuery.id == query_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Supportive query {query_id} not found")

        db.delete(record)
        db.commit()
        
        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

        db.close()

        return {"success": True, "deleted_id": query_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Rules & Closure Notes Endpoints

@router.get("/api/db/rules", tags=["Rules"])
def list_rules():
    # List all available ES correlation rules, including any supportive queries.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, SupportiveQuery
        db = SessionLocal()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()

        # Preload supportive queries for all rules
        all_supportive = db.query(SupportiveQuery).all()
        db.close()

        # Older imported databases can contain enabled rule rows with a
        # missing rule_name. Use the checked-in rule catalog as a read-only
        # display fallback so closure selection remains understandable without
        # requiring a database migration.
        catalog_by_id: Dict[str, Dict[str, Any]] = {}
        for catalog_path in [
            Path(get_platform_root()) / "updated_rules.json",
            Path(__file__).resolve().parent.parent / "updated_rules.json",
        ]:
            try:
                if catalog_path.exists():
                    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
                    catalog_by_id = {
                        str(item.get("rule_id")): item
                        for item in (catalog_payload.get("rules") or [])
                        if item.get("rule_id")
                    }
                    if catalog_by_id:
                        break
            except Exception:
                continue

        by_rule: Dict[str, List[Dict[str, Any]]] = {}
        for sq in all_supportive:
            by_rule.setdefault(sq.rule_id, []).append({
                "id": sq.id,
                "title": sq.title,
                "description": sq.description,
                "spl_query": sq.spl_query,
            })

        def supportive_for_rule(rule_obj) -> List[Dict[str, Any]]:
            # Return supportive queries for a given rule.
            # In addition to queries explicitly keyed to this rule_id, we
            # support lightweight family sharing for LotL-style rules: any
            # rule whose ID starts with ``lotl_`` automatically inherits the
            # supportive queries defined under the canonical
            # ``lotl_outbound_connection`` family, unless duplicates exist.
            # This lets future LotL notables reuse the same investigation
            # SPL without duplicating query definitions in the database.

            rule_id = (rule_obj.rule_id or "").strip()
            base = list(by_rule.get(rule_id, []))

            # LotL family sharing: treat any "lotl_*" rule as part of the
            # same investigative family and reuse the canonical queries.
            canonical_lotl_id = "lotl_outbound_connection"
            if rule_id.startswith("lotl_") and rule_id != canonical_lotl_id:
                extras = by_rule.get(canonical_lotl_id, []) or []
                existing_titles = {q["title"] for q in base}
                for q in extras:
                    if q["title"] not in existing_titles:
                        base.append(q)

            return base

        response_rules = [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name or catalog_by_id.get(r.rule_id, {}).get("rule_name") or r.rule_id,
                "description": r.description or catalog_by_id.get(r.rule_id, {}).get("description") or "",
                "category": r.category or catalog_by_id.get(r.rule_id, {}).get("category") or "",
                "severity": r.severity or catalog_by_id.get(r.rule_id, {}).get("severity") or "medium",
                "drilldown_fields": json.loads(r.drilldown_fields) if r.drilldown_fields else catalog_by_id.get(r.rule_id, {}).get("drilldown_fields", []),
                "required_closure_fields": json.loads(r.required_closure_fields) if r.required_closure_fields else catalog_by_id.get(r.rule_id, {}).get("required_closure_fields", []),
                "supportive_queries": supportive_for_rule(r),
            }
            for r in rules
        ]

        # Analyst-created unsupported rules do not necessarily have an
        # ESCorrelationRule row yet. Once their reviewed supportive queries
        # are saved, expose them as synthetic rule entries so the analysis UI
        # can resolve the active case and render the new playbook immediately.
        known_rule_ids = {str(item.get("rule_id") or "").strip() for item in response_rules}
        for rule_id, queries in by_rule.items():
            normalized_id = str(rule_id or "").strip()
            if not normalized_id or normalized_id in known_rule_ids:
                continue
            catalog_item = catalog_by_id.get(normalized_id, {})
            fallback_name = normalized_id.replace("_", " ").strip().title() or normalized_id
            response_rules.append({
                "rule_id": normalized_id,
                "rule_name": catalog_item.get("rule_name") or fallback_name,
                "description": catalog_item.get("description") or "Analyst-created supportive playbook rule",
                "category": catalog_item.get("category") or "custom",
                "severity": catalog_item.get("severity") or "medium",
                "drilldown_fields": catalog_item.get("drilldown_fields") or [],
                "required_closure_fields": catalog_item.get("required_closure_fields") or [],
                "supportive_queries": queries,
            })

        return response_rules
    except Exception:
        return []

