"""Inspect the current SOC Platform database schema and row counts.

This helper now targets the configured runtime database (PostgreSQL)
via SQLAlchemy instead of the legacy SQLite triage.db file.

Usage (from project root):

    # Uses DATABASE_URL or the default Postgres URL from db.models
    python inspect_db.py
"""

from __future__ import annotations

import json
import os
import sys

from sqlalchemy import create_engine, inspect, text


def get_database_url() -> str:
    """Resolve the database URL in the same way as the API runtime."""
    repo_root = os.getcwd()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from db.models import DATABASE_URL

    return os.environ.get("DATABASE_URL", DATABASE_URL)


def main() -> int:
    url = get_database_url()
    engine = create_engine(url)
    inspector = inspect(engine)

    tables = inspector.get_table_names()
    print(f"Found {len(tables)} tables in {url}:\n")

    schema: dict[str, list[str]] = {}

    with engine.connect() as conn:
        for table_name in tables:
            print(f"\n{table_name}:")

            columns = inspector.get_columns(table_name)
            col_info: list[str] = []
            for col in columns:
                name = col.get("name")
                col_type = str(col.get("type"))
                col_info.append(f"  {name} ({col_type})")
                print(f"  {name} ({col_type})")

            schema[table_name] = col_info

            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
            print(f"  Rows: {count}")

    print("\n\n=== SCHEMA JSON ===")
    print(json.dumps(schema, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
