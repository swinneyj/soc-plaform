#!/usr/bin/env python3
"""
Wipe SOC Platform triage / notable / analysis tables so you can load clean dummy data.

Safe tables only (does not drop schema, rules, or placeholder aliases).
Run from project root with DATABASE_URL and SOC_PLATFORM_ROOT set, or let the
PowerShell runner set them for you.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Ensure we can import db.models from the project root
    repo_root = os.environ.get("SOC_PLATFORM_ROOT") or os.getcwd()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if not os.environ.get("SOC_PLATFORM_ROOT"):
        os.environ["SOC_PLATFORM_ROOT"] = repo_root

    # Prefer explicit DATABASE_URL; otherwise models.py falls back to its default
    # (Postgres on localhost:5433 when not in /app).
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = (
            "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
        )

    from db.models import (
        AnalysisResult,
        ClosureNote,
        InvestigationState,
        SessionLocal,
        SplunkEvent,
        SupportiveQueryResult,
        TriageResult,
    )

    models = [
        SupportiveQueryResult,
        InvestigationState,
        AnalysisResult,
        ClosureNote,
        TriageResult,
        SplunkEvent,
    ]

    db = SessionLocal()
    try:
        print("Wiping SOC Platform operational tables...")
        total = 0
        for model in models:
            try:
                n = db.query(model).delete()
                print(f"  {model.__tablename__:30s}  deleted {n}")
                total += n
            except Exception as exc:
                print(f"  {model.__tablename__:30s}  ERROR: {exc}")
                db.rollback()
                return 1
        db.commit()
        print(f"Wipe complete. Total rows removed: {total}")
        return 0
    except Exception as exc:
        print(f"FATAL: {exc}")
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
