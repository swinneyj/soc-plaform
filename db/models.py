"""SQLAlchemy models and database bootstrap for SOC Platform.

The platform runtime database is PostgreSQL only. Legacy SQLite backup
files are no longer used as a fallback at runtime.
"""

from datetime import datetime, timezone
import os

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from db.crypto import EncryptedText


def get_default_postgres_url() -> str:
    """URL used when `DATABASE_URL` is unset — the same rule as the launchers.

    Delegates to `db.target` so a bare process (a one-off script, a manually
    started uvicorn, `python scripts/manage_users.py`) lands on the **local
    primary** — 127.0.0.1:5432 — and honours `SOC_DB_TARGET`. It used to
    hardcode the compose host port 5433, which meant tools run outside the
    launchers quietly aimed at a port nothing was serving (or, worse, at a
    second database). `db.target` is stdlib-only, so importing it here costs
    nothing and cannot recurse (it deliberately does not import this module).

    The container default (`/app` → host `postgres`) is handled inside
    `db.target.local_url()`; docker-compose always sets `DATABASE_URL` itself,
    so the exposed host port 5433 is unaffected.
    """
    from db.target import resolve

    return resolve()[0]


DATABASE_URL = os.environ.get("DATABASE_URL") or get_default_postgres_url()


def _utcnow() -> datetime:
    """Naive UTC now (platform convention) without the deprecated
    datetime.utcnow() — safe as a Column default/callable."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_database_url(database_url: str) -> str:
    """Use the psycopg driver for standard Neon/Postgres URLs."""
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    return database_url


def create_database_engine(database_url: str, connect_args: dict | None = None):
    """Create a SQLAlchemy engine for the configured runtime database.

    The supported runtime backend is PostgreSQL. Other drivers are not
    configured or tested here. `connect_args` (e.g. {"connect_timeout": 2})
    is passed through to the DBAPI — used by the /api/health probe so it
    fails fast instead of hanging on a dead database.
    """
    kwargs = {"pool_pre_ping": True}
    if connect_args:
        kwargs["connect_args"] = connect_args
    return create_engine(normalize_database_url(database_url), **kwargs)


engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class TriageResult(Base):
    """Triage results from Splunk ES backup toolkit."""
    __tablename__ = "triage_results"
    
    case_id = Column(String, primary_key=True, index=True)
    rule_name = Column(String, index=True)
    rule_id = Column(String, index=True, nullable=True)  # FK to ESCorrelationRule
    verdict = Column(String)  # e.g., "benign", "suspicious", "malicious"
    confidence_score = Column(Float)  # 0.0 to 1.0
    analysis_summary = Column(Text)
    remediation_steps = Column(Text)
    triaged_at = Column(DateTime, default=_utcnow, index=True)

class SplunkEvent(Base):
    """Ingested Splunk events for local analysis."""
    __tablename__ = "splunk_events"
    
    id = Column(Integer, primary_key=True, index=True)
    sourcetype = Column(String, index=True)
    source = Column(String)
    host = Column(String, index=True)
    raw = Column(Text)  # Raw event data
    timestamp = Column(DateTime, index=True)
    ingested_at = Column(DateTime, default=_utcnow)
    
class AnalysisResult(Base):
    """AI-generated analysis results using Ollama."""
    __tablename__ = "analysis_results"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, index=True)
    model_name = Column(String)  # e.g., "llama2", "mistral"
    query = Column(Text)  # The prompt sent to Ollama
    analysis = Column(Text)  # The LLM response
    confidence = Column(Float)  # Model's confidence
    created_at = Column(DateTime, default=_utcnow, index=True)

class ESCorrelationRule(Base):
    """Splunk Enterprise Security correlation rules."""
    __tablename__ = "es_correlation_rules"
    
    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(String, unique=True, index=True)
    rule_name = Column(String, index=True)
    description = Column(Text)
    category = Column(String, index=True)
    severity = Column(String)
    drilldown_fields = Column(Text)  # JSON array
    required_closure_fields = Column(Text)  # JSON array
    closure_template = Column(Text)
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

class ClosureNote(Base):
    """Generated closure notes for incidents."""
    __tablename__ = "closure_notes"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, index=True)
    rule_id = Column(String, index=True)
    analyst_notes = Column(Text)
    generated_note = Column(Text)
    status = Column(String, default="draft")  # draft, submitted, closed
    created_at = Column(DateTime, default=_utcnow)
    submitted_at = Column(DateTime)

class SupportiveQuery(Base):
    """Supportive SPL queries tied to specific correlation rules."""
    __tablename__ = "supportive_queries"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(String, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    spl_query = Column(Text)
    created_at = Column(DateTime, default=_utcnow, index=True)


class SupportiveQueryResult(Base):
    """Stored results from running supportive queries for a specific case.

    This lets the SOC app and local models consume enrichment/evidence
    generated by running supportive SPL/SQL outside the platform.
    """

    __tablename__ = "supportive_query_results"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, index=True)
    rule_id = Column(String, index=True)
    query_title = Column(String, index=True)
    source_system = Column(String, default="splunk")  # e.g., splunk, sql, other
    raw_result = Column(Text)  # JSON or text blob of the query output
    created_at = Column(DateTime, default=_utcnow, index=True)


class InvestigationState(Base):
    """Persistent investigation loop state for a triage case.

    This tracks the current working hypothesis, evidence balance, open
    questions, closure blockers, and next best actions across repeated
    analysis/evidence-save cycles.
    """

    __tablename__ = "investigation_states"

    case_id = Column(String, primary_key=True, index=True)
    rule_id = Column(String, index=True)
    current_hypothesis = Column(Text)
    provisional_disposition = Column(String, index=True)
    disposition_confidence = Column(Float)
    loop_status = Column(String, index=True)
    iteration_count = Column(Integer, default=0)
    unresolved_questions = Column(Text)  # JSON array
    closure_blockers = Column(Text)  # JSON array
    recommended_next_actions = Column(Text)  # JSON array
    evidence_summary = Column(Text)  # JSON object
    last_analysis_stage = Column(String, default="initial")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, index=True)


class PlaceholderAlias(Base):
    """Dynamic placeholder aliases for supportive SPL templates.

    Each alias maps a short logical name (e.g., "host", "dest", "user")
    to a list of candidate notable fields that can supply values when
    rendering supportive queries with $placeholder$ tokens.

    The `fields` column is stored as a JSON-encoded list of strings to keep
    the schema simple and portable across relational database backends.
    """

    __tablename__ = "placeholder_aliases"

    id = Column(Integer, primary_key=True, index=True)
    alias = Column(String, unique=True, index=True)
    fields = Column(Text)  # JSON-encoded ["field1", "field2", ...]
    description = Column(Text)


class CodeReview(Base):
    """Code review results from Ollama LLM analysis.

    ``code_snippet`` is encrypted at rest (SEC-03, ``db.crypto``) — it is the
    most sensitive free text this platform stores.
    """
    __tablename__ = "code_reviews"

    id = Column(Integer, primary_key=True, index=True)
    code_snippet = Column(EncryptedText)
    language = Column(String, default="python")
    review_result = Column(Text)
    model_name = Column(String, default="llama3.1:latest")
    created_at = Column(DateTime, default=_utcnow, index=True)


class ToolRun(Base):
    """Persistent execution record shared by every catalog tool."""

    __tablename__ = "tool_runs"

    job_id = Column(String, primary_key=True, index=True)
    tool_name = Column(String, index=True)
    arguments = Column(Text)  # JSON object
    status = Column(String, index=True, default="pending")
    stdout = Column(Text)
    stderr = Column(Text)
    exit_code = Column(Integer)
    artifact_paths = Column(Text)  # JSON array of repo-relative paths
    created_at = Column(DateTime, default=_utcnow, index=True)
    completed_at = Column(DateTime)


class User(Base):
    """Operator account for session auth (SESSION_AUTH_PLAN.md Piece A).

    Inert until AUTH_MODE=session; the API-key gate never reads these rows.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)  # scrypt$n$r$p$salthex$hashhex
    role = Column(String(16), nullable=False, default="analyst")  # analyst|admin
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class AuthSession(Base):
    """Live browser session. Stores only the SHA-256 hash of the cookie value.
    """

    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)  # sha256 hex
    # SEC-03: encrypted at rest. The ciphertext (~104 chars for a 43-char
    # token) does not fit the old String(64); the decorator's impl is TEXT, so
    # fresh databases are fine and existing ones need the ALTER in
    # docs/COLUMN_ENCRYPTION.md before a key is enabled.
    csrf_token = Column(EncryptedText, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)


def get_db():
    """Dependency for FastAPI to get DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
