"""Migrate SOC Platform data from SQLite to PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.models import Base, get_default_sqlite_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate SOC Platform data from SQLite to PostgreSQL")
    parser.add_argument(
        "--source-url",
        default=os.environ.get("SOURCE_DATABASE_URL", get_default_sqlite_url()),
        help="SQLAlchemy URL for the source database. Defaults to the local SQLite DB.",
    )
    parser.add_argument(
        "--target-url",
        default=os.environ.get("TARGET_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="SQLAlchemy URL for the target database. Required for migration.",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Delete rows from target tables before copying source rows.",
    )
    return parser


def reflect_source_tables(source_engine) -> MetaData:
    source_meta = MetaData()
    source_meta.reflect(bind=source_engine)
    return source_meta


def migrate(source_url: str, target_url: str, drop_existing: bool) -> dict:
    if not target_url:
        raise ValueError("A target database URL is required.")
    if not target_url.startswith("postgresql"):
        raise ValueError("Target URL must be a PostgreSQL SQLAlchemy URL.")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    Base.metadata.create_all(target_engine)

    source_meta = reflect_source_tables(source_engine)
    target_inspector = inspect(target_engine)
    target_tables = set(target_inspector.get_table_names())

    migrated = {}
    skipped = []

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        for table_name in sorted(source_meta.tables.keys()):
            if table_name not in target_tables:
                skipped.append(table_name)
                continue

            source_table = source_meta.tables[table_name]
            target_table = Table(table_name, MetaData(), autoload_with=target_engine)
            rows = [dict(row) for row in source_conn.execute(select(source_table)).mappings()]

            if drop_existing:
                target_conn.execute(text(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY'))

            if rows:
                target_conn.execute(target_table.insert(), rows)
            migrated[table_name] = len(rows)

    return {
        "source_url": source_url,
        "target_url": target_url,
        "migrated": migrated,
        "skipped": skipped,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = migrate(args.source_url, args.target_url, args.drop_existing)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()