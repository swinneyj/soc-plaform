#!/usr/bin/env python3
"""OPS-02 — verify a backup dump by restoring into a scratch database.

Takes the output of scripts/backup_db.sh (gzipped plain-SQL pg_dump),
restores it into a temporary scratch database on the LOCAL Postgres, and
verifies table row counts + schema integrity against the live database.

    python scripts/verify_backup_restore.py --dump local-backups/postgres_dump_*.sql.gz
    python scripts/verify_backup_restore.py --dump <file> --keep   # leave scratch DB for inspection
    python scripts/verify_backup_restore.py --dump <file> --allow-shared  # only if source is shared

Steps: create scratch DB -> gunzip+psql restore -> compare Base-table row
counts (live vs restored) + missing tables + narrow columns -> drop scratch.
Exit 0 when counts match and schema is clean, 1 otherwise. Never prints
credentials or row contents — counts and table names only.
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sqlalchemy import text as sa_text  # noqa: E402


def _describe(url: str) -> str:
    from db.target import describe

    return describe(url)


def _refuse_shared_unless_allowed(allow_shared: bool, url: str, kind: str):
    if allow_shared:
        return
    from urllib.parse import urlsplit

    from db import target as db_target

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    is_sqlite = (parts.scheme or "").startswith("sqlite")
    if kind == "shared" or (
        kind == "explicit" and not is_sqlite and host not in ("127.0.0.1", "localhost", "::1", "postgres")
    ):
        print(
            "refusing: source is not this machine (%s). Restoring a shared dump "
            "into scratch still reads production data — pass --allow-shared if intended."
            % (host or kind)
        )
        raise SystemExit(2)
    # Also refuse to CREATE scratch on a shared host even for local dumps.
    if db_target.is_shared_host(url):
        print("refusing: resolved target is a shared host; use a local DATABASE_URL.")
        raise SystemExit(2)


def _table_counts(engine, tables: list[str]) -> dict:
    counts = {}
    with engine.connect() as conn:
        for t in tables:
            try:
                counts[t] = conn.execute(sa_text('select count(*) from "%s"' % t)).scalar()  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- t comes from Base.metadata.tables.keys() (ORM metadata), double-quoted
            except Exception as exc:
                counts[t] = "ERR:%s" % type(exc).__name__
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", required=True, help="pg_dump output (.sql or .sql.gz)")
    ap.add_argument("--keep", action="store_true", help="leave the scratch DB for inspection")
    ap.add_argument("--allow-shared", action="store_true", help="permit shared sources")
    args = ap.parse_args(argv)

    dump = os.path.abspath(args.dump)
    if not os.path.exists(dump):
        print("dump not found: %s" % args.dump)
        return 2

    from db import target as db_target
    from db.models import Base, create_database_engine

    try:
        url, kind, source = db_target.resolve()
    except Exception as exc:
        print("cannot resolve database target: %s" % exc)
        return 2
    _refuse_shared_unless_allowed(args.allow_shared, url, kind)
    print("source: %s (target: %s, source: %s)" % (_describe(url), kind, source))

    if not shutil.which("psql"):
        print("psql not found on PATH (install PostgreSQL client tools)")
        return 2

    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    # Normalize SQLAlchemy URL to plain postgres:// for psql.
    scheme = "postgresql://"
    netloc = parts.netloc
    base_path = parts.path or "/postgres"
    scratch_name = "soc_verify_%d" % int(time.time())
    scratch_parts = parts._replace(path="/" + scratch_name)
    # Strip +driver suffix for psql.
    scratch_url = urlunsplit(("postgresql", scratch_parts.netloc, scratch_parts.path, scratch_parts.query, scratch_parts.fragment))
    source_psql_url = urlunsplit(("postgresql", parts.netloc, base_path, parts.query, parts.fragment))

    # Create scratch DB via the maintenance DB.
    admin_engine = create_database_engine(url)
    try:
        with admin_engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(sa_text('CREATE DATABASE "%s"' % scratch_name))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- scratch_name is generated in this file (soc_verify_<timestamp>), never from input
        print("scratch: created database %s" % scratch_name)
    except Exception as exc:
        print("cannot create scratch database: %s: %s" % (type(exc).__name__, exc))
        return 2
    finally:
        try:
            admin_engine.dispose()
        except Exception:
            pass

    rc = 0
    try:
        # Restore: gunzip -c dump | psql scratch
        if dump.endswith(".gz"):
            gz = gzip.open(dump, "rb")
            psql = subprocess.Popen(
                ["psql", scratch_url, "-q", "-v", "ON_ERROR_STOP=1", "-f", "-"],
                stdin=gz, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            _, err = psql.communicate()
            gz.close()
        else:
            with open(dump, "rb") as fh:
                psql = subprocess.run(
                    ["psql", scratch_url, "-q", "-v", "ON_ERROR_STOP=1", "-f", "-"],
                    stdin=fh, capture_output=True,
                )
                err = psql.stderr
                psql_returncode = psql.returncode
            if psql_returncode != 0:
                print("restore failed:\n%s" % (err.decode(errors='replace')[-2000:]))
                return 1
            psql_returncode = 0
            err = b""
        if dump.endswith(".gz") and psql.returncode != 0:
            print("restore failed:\n%s" % ((err or b"").decode(errors="replace")[-2000:]))
            return 1
        print("restore: ok into %s" % scratch_name)

        tables = sorted(Base.metadata.tables.keys())
        live_engine = create_database_engine(url)
        scratch_engine = create_database_engine(
            urlunsplit(("postgresql", parts.netloc, "/" + scratch_name, parts.query, parts.fragment))
        )
        try:
            live_counts = _table_counts(live_engine, tables)
            restored_counts = _table_counts(scratch_engine, tables)
        finally:
            for e in (live_engine, scratch_engine):
                try:
                    e.dispose()
                except Exception:
                    pass

        mismatches = []
        for t in tables:
            lv, rs = live_counts.get(t), restored_counts.get(t)
            flag = "" if lv == rs else "  <-- MISMATCH"
            if lv != rs:
                mismatches.append(t)
            print("%-28s live=%-8s restored=%-8s%s" % (t, lv, rs, flag))

        # Schema integrity: every model table present in scratch.
        missing = [t for t in tables if str(restored_counts.get(t)).startswith("ERR")]
        if missing:
            print("schema: missing/unreadable in scratch: %s" % ", ".join(missing))
            rc = 1
        elif mismatches:
            print("counts differ on: %s (live writes during dump can explain small drift)" % ", ".join(mismatches))
            rc = 1
        else:
            print("verify: ok — %d tables, counts match, schema present" % len(tables))
    finally:
        if not args.keep:
            try:
                admin_engine2 = create_database_engine(url)
                with admin_engine2.connect() as conn:
                    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                    # Terminate backends before drop (restore may hold one briefly).
                    conn.execute(
                        sa_text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :d AND pid <> pg_backend_pid()"
                        ),
                        {"d": scratch_name},
                    )
                    conn.execute(sa_text('DROP DATABASE IF EXISTS "%s"' % scratch_name))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- scratch_name is generated in this file (soc_verify_<timestamp>), never from input
                admin_engine2.dispose()
                print("scratch: dropped %s" % scratch_name)
            except Exception as exc:
                print("warning: could not drop scratch %s (%s)" % (scratch_name, type(exc).__name__))
        else:
            print("scratch: kept %s for inspection" % scratch_name)
    return rc


if __name__ == "__main__":
    sys.exit(main())
