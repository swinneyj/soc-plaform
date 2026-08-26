"""Fix Postgres sequence for supportive_queries.id to avoid duplicate PK errors.

This script realigns the auto-increment sequence backing supportive_queries.id
(to the current MAX(id) + 1) for Postgres deployments. Run it once if you see
errors like:

  psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
  "supportive_queries_pkey" (Key (id)=(1) already exists.)

It is safe to run multiple times; on non-Postgres databases it exits without
changes.
"""

from __future__ import annotations

import sys

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # type: ignore
from db.models import engine  # noqa: E402


def main() -> None:
    driver = engine.url.drivername
    if not driver.startswith("postgresql"):
        print("supportive_queries sequence fix is only needed for Postgres; current driver:", driver)
        return

    with engine.connect() as conn:
        # Align the serial sequence for supportive_queries.id to MAX(id)+1.
        # pg_get_serial_sequence is used so we do not rely on a hard-coded
        # sequence name.
        stmt = text(
            "SELECT setval("
            "  pg_get_serial_sequence('supportive_queries', 'id'),"
            "  COALESCE((SELECT MAX(id) + 1 FROM supportive_queries), 1),"
            "  false"
            ")"
        )
        result = conn.execute(stmt)
        new_val = result.scalar()
        print("supportive_queries.id sequence realigned; new next value:", new_val)


if __name__ == "__main__":
    main()
