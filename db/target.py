"""Which database the platform talks to — and why.

Local Postgres is the **primary** runtime database. The shared Neon project is
an optional mirror kept for coworker-visible data; nothing in the default
launch path requires it, so a free-tier storage or egress ceiling can never
stop the platform from running.

Resolution order (first match wins):

1. ``DATABASE_URL`` set explicitly (shell or ``.env``) — operator intent, and
   the pre-existing behaviour. The launchers deliberately do not overwrite it.
2. ``SOC_DB_TARGET=shared`` — ``SHARED_DATABASE_URL`` (the launchers fill it
   from BWS or the keychain). Missing here is a **refusal**, never a silent
   fallback to local: falling back would quietly split the shared dataset
   between two machines without anyone noticing.
3. Otherwise **local** — ``LOCAL_DATABASE_URL`` if set, else the built-in host
   default (127.0.0.1:5432), else the container default when running under
   ``/app``.

``describe()`` reports host/port/database with credentials stripped, so status
lines can be pasted into logs or chat. Nothing in this module prints a
password.

CLI (used by the shell launchers so URL construction lives in one place):

    python -m db.target --print         # resolved URL on stdout
    python -m db.target --summary       # one line: target host/db (source)
    python -m db.target --check         # target + source + redacted host (+ ping)
    python -m db.target --ping          # connect, report tables/rows (local-first)
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional, Tuple
from urllib.parse import urlsplit

LOCAL_DEFAULT_URL = "postgresql+psycopg://soc_platform@127.0.0.1:5432/soc_platform"
CONTAINER_DEFAULT_URL = "postgresql+psycopg://soc_platform@postgres:5432/soc_platform"
VALID_TARGETS = ("local", "shared")


class TargetError(RuntimeError):
    """Ambiguous or incomplete database target — callers must not guess."""


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def default_target(environ: Optional[Mapping[str, str]] = None) -> str:
    """`SOC_DB_TARGET`, defaulting to `local`; unknown values are refused."""
    raw = (_env(environ).get("SOC_DB_TARGET") or "local").strip().lower()
    if raw not in VALID_TARGETS:
        raise TargetError(
            "SOC_DB_TARGET=%r is not one of: %s" % (raw, ", ".join(VALID_TARGETS))
        )
    return raw


def local_url(environ: Optional[Mapping[str, str]] = None) -> str:
    """The local Postgres URL: explicit override, container default, else host."""
    override = (_env(environ).get("LOCAL_DATABASE_URL") or "").strip()
    if override:
        return override
    if os.path.exists("/app"):
        return CONTAINER_DEFAULT_URL
    return LOCAL_DEFAULT_URL


def resolve(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, str, str]:
    """Return `(url, target, source)` for the current environment.

    `target` is `explicit`, `local` or `shared`; `source` names the variable
    the URL came from so `--check` can explain a surprising connection.
    """
    env = _env(environ)
    explicit = (env.get("DATABASE_URL") or "").strip()
    if explicit:
        return explicit, "explicit", "DATABASE_URL"

    target = default_target(env)
    if target == "shared":
        shared = (env.get("SHARED_DATABASE_URL") or "").strip()
        if not shared:
            raise TargetError(
                "SOC_DB_TARGET=shared but SHARED_DATABASE_URL is empty. "
                "Set it (BWS/keychain) or drop SOC_DB_TARGET to run local."
            )
        return shared, "shared", "SHARED_DATABASE_URL"

    source = "LOCAL_DATABASE_URL" if (env.get("LOCAL_DATABASE_URL") or "").strip() else "built-in local default"
    return local_url(env), "local", source


def describe(url: str) -> str:
    """Host/port/database for display. Never includes userinfo or query args."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable URL)"
    host = parts.hostname or "(no host)"
    port = parts.port
    database = (parts.path or "").lstrip("/") or "(no database)"
    label = host + (":%d" % port if port else "")
    return "%s/%s" % (label, database)


def is_shared_host(url: str) -> bool:
    """True for the Neon-backed shared mirror (by host, case-insensitive)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.endswith("neon.tech")


def _normalize_url(url: str) -> str:
    """Same driver coercion as `db.models.normalize_database_url`.

    Duplicated deliberately: this module is imported by the launchers before a
    target is known, so it must not pull in `db.models` (which builds the
    module-level engine from `DATABASE_URL`).
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def ping(url: str, timeout: int = 5) -> Tuple[bool, str]:
    """Connect and report a cheap schema/count fact. Returns `(ok, message)`."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(
            _normalize_url(url),
            pool_pre_ping=True,
            connect_args={"connect_timeout": timeout},
        )
        with engine.connect() as connection:
            tables = connection.execute(
                text(
                    "select count(*) from information_schema.tables "
                    "where table_schema = 'public'"
                )
            ).scalar_one()
            cases = None
            if tables:
                try:
                    cases = connection.execute(
                        text("select count(*) from triage_results")
                    ).scalar_one()
                except Exception:  # table missing on a fresh database
                    cases = None
        detail = "%d tables" % tables
        if cases is not None:
            detail += ", %d triage rows" % cases
        return True, detail
    except Exception as exc:  # connection, auth, driver — all report the same way
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return False, first[:160]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "--check"
    try:
        url, target, source = resolve()
    except TargetError as exc:
        print("✗ %s" % exc, file=sys.stderr)
        return 2

    if mode == "--print":
        print(url)
        return 0
    if mode == "--summary":
        # One line, no connection: for launch banners and status blocks.
        print("%s %s (source: %s)" % (target, describe(url), source))
        return 0
    if mode == "--check":
        print("target: %s (source: %s)" % (target, source))
        print("host:   %s" % describe(url))
        if target == "local" or not is_shared_host(url):
            ok, detail = ping(url)
            print("connect: %s — %s" % ("ok" if ok else "FAILED", detail))
            return 0 if ok else 1
        print("connect: not probed (shared host; launch with --shared to use it)")
        return 0
    if mode == "--ping":
        ok, detail = ping(url)
        print(("ok — " if ok else "FAILED — ") + detail)
        return 0 if ok else 1
    print(
        "usage: python -m db.target [--print|--summary|--check|--ping]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
