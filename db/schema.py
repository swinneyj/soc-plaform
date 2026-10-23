"""Create tables the models declare but the target database lacks.

`create_all()` is additive by contract: it issues CREATE TABLE for missing
tables only — never a DROP, never an ALTER. That is enough for the local-first
launch path, because the local database can predate a model (it was missing
`users` and `auth_sessions`, which session auth needs), and it stays safe to
run on every start.

It **refuses the shared target** unless ``--allow-shared`` is passed: creating
tables on the shared Neon project mutates coworker-visible data, and that must
never be a side effect of "start the API locally". A shared schema is managed
by whoever owns that project, not by a launcher.

CLI:
    python -m db.schema                 # create missing tables (local only)
    python -m db.schema --check         # report only; exit 1 if drift
    python -m db.schema --allow-shared  # explicit opt-in for the shared DB
"""

from __future__ import annotations

import sys
from typing import List

from sqlalchemy import inspect

from db import target as db_target
from db.models import Base, create_database_engine


def missing_tables(url: str) -> List[str]:
    """Model tables absent from the database at `url` (read-only)."""
    engine = create_database_engine(url)
    try:
        present = set(inspect(engine).get_table_names())
    finally:
        pass
    return sorted(name for name in Base.metadata.tables if name not in present)


def ensure_schema(url: str) -> List[str]:
    """Create the missing tables; return the names actually created."""
    before = missing_tables(url)
    if not before:
        return []
    engine = create_database_engine(url)
    Base.metadata.create_all(bind=engine)
    after = set(inspect(engine).get_table_names())
    return sorted(name for name in before if name in after)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    allow_shared = "--allow-shared" in argv
    check_only = "--check" in argv
    if any(arg not in ("--allow-shared", "--check") for arg in argv):
        print("usage: python -m db.schema [--check] [--allow-shared]", file=sys.stderr)
        return 2

    try:
        url, kind, source = db_target.resolve()
    except db_target.TargetError as exc:
        print("✗ %s" % exc, file=sys.stderr)
        return 2

    shared = kind == "shared" or db_target.is_shared_host(url)
    if shared and not allow_shared:
        print(
            "✗ refusing to touch the shared target (%s, source: %s).\n"
            "  Schema changes on the shared project are deliberate operations;\n"
            "  pass --allow-shared if that is genuinely what you want."
            % (db_target.describe(url), source),
            file=sys.stderr,
        )
        return 2

    if check_only:
        missing = missing_tables(url)
        if missing:
            print("schema drift: missing %s" % ", ".join(missing))
            return 1
        print("schema: ok — every model table exists")
        return 0

    created = ensure_schema(url)
    if created:
        print("created %s" % ", ".join(created))
    else:
        print("schema: ok — nothing to create")
    return 0


if __name__ == "__main__":
    sys.exit(main())
