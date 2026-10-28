"""Local-first database target resolution (db.target / db.schema).

Local Postgres is the primary runtime database; the shared Neon project is an
optional mirror. These tests pin the properties that make that safe:

* the default is local, with no vault or keychain involved;
* an explicit ``DATABASE_URL`` still wins (operator intent, and the behaviour
  every existing script and doc assumes);
* ``SOC_DB_TARGET=shared`` without a shared URL is a **refusal**, never a
  silent fallback to local — a fallback would split the shared dataset
  between two machines without anyone noticing;
* passwords never appear in the status strings meant for logs/chat;
* the schema bootstrap refuses the shared target unless explicitly allowed.

No database connection is required: every case either resolves a URL from an
environment mapping or exits before connecting.
"""

import pytest

from db import schema as db_schema
from db import target as db_target

LOCAL = "postgresql+psycopg://soc_platform@127.0.0.1:5432/soc_platform"
NEON = "postgresql+psycopg://user:sup3rsecret@ep-cool-name-123.eu-central-1.aws.neon.tech/neondb"


def test_default_target_is_local_without_any_shared_config():
    url, target, source = db_target.resolve({})
    assert target == "local"
    assert url == LOCAL
    assert source == "built-in local default"


def test_local_database_url_overrides_the_builtin_default():
    url, target, source = db_target.resolve(
        {"LOCAL_DATABASE_URL": "postgresql+psycopg://soc_platform@127.0.0.1:5433/soc_platform"}
    )
    assert target == "local"
    assert url.endswith(":5433/soc_platform")
    assert source == "LOCAL_DATABASE_URL"


def test_explicit_database_url_wins_over_the_target():
    url, target, source = db_target.resolve({"DATABASE_URL": NEON, "SOC_DB_TARGET": "local"})
    assert (url, target, source) == (NEON, "explicit", "DATABASE_URL")


def test_shared_target_uses_the_shared_url():
    url, target, source = db_target.resolve(
        {"SOC_DB_TARGET": "shared", "SHARED_DATABASE_URL": NEON}
    )
    assert (url, target, source) == (NEON, "shared", "SHARED_DATABASE_URL")


def test_shared_target_without_a_url_is_refused_not_fallen_back():
    """The one property that keeps two datasets from silently diverging."""
    with pytest.raises(db_target.TargetError) as excinfo:
        db_target.resolve({"SOC_DB_TARGET": "shared"})
    message = str(excinfo.value)
    assert "SHARED_DATABASE_URL" in message
    assert "local" in message  # tells the operator how to go back to local


def test_unknown_target_is_refused():
    with pytest.raises(db_target.TargetError):
        db_target.resolve({"SOC_DB_TARGET": "neon"})


def test_describe_never_leaks_credentials():
    described = db_target.describe(NEON)
    assert "sup3rsecret" not in described
    assert "user@" not in described
    assert described == "ep-cool-name-123.eu-central-1.aws.neon.tech/neondb"


def test_describe_survives_a_malformed_url():
    described = db_target.describe("not-a-url")
    assert isinstance(described, str) and described
    assert "(no host)" in described


def test_is_shared_host_is_host_based_not_name_based():
    assert db_target.is_shared_host(NEON) is True
    assert db_target.is_shared_host("postgresql://u:p@ep-x.neon.tech/db") is True
    # A bare host (no scheme) is not a URL; refusing it is safer than guessing.
    assert db_target.is_shared_host("ep-x.neon.tech") is False
    assert db_target.is_shared_host(LOCAL) is False
    # A database *named* neon on a local server is still local.
    assert db_target.is_shared_host("postgresql://soc_platform@127.0.0.1:5432/neon") is False


def test_schema_bootstrap_refuses_the_shared_target(monkeypatch):
    """Shared schema changes are deliberate operations, never a launcher side effect."""
    monkeypatch.setenv("SOC_DB_TARGET", "shared")
    monkeypatch.setenv("SHARED_DATABASE_URL", NEON)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert db_schema.main(["--check"]) == 2
    assert db_schema.main([]) == 2


def test_schema_bootstrap_rejects_unknown_flags():
    assert db_schema.main(["--drop-everything"]) == 2


def test_target_cli_reports_a_bad_target_on_stderr(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SOC_DB_TARGET", "sideways")
    assert db_target.main(["--print"]) == 2
    captured = capsys.readouterr()
    assert "SOC_DB_TARGET" in captured.err
    assert captured.out == ""


def test_target_cli_print_emits_only_the_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SOC_DB_TARGET", raising=False)
    monkeypatch.delenv("LOCAL_DATABASE_URL", raising=False)
    assert db_target.main(["--print"]) == 0
    assert capsys.readouterr().out.strip() == LOCAL


def test_models_default_follows_local_first(monkeypatch):
    """A bare process (no DATABASE_URL) must aim at the local primary.

    db.models used to hardcode the compose host port 5433 here, so one-off
    scripts run outside the launchers silently targeted the wrong database.
    """
    from db import models

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SOC_DB_TARGET", raising=False)
    monkeypatch.delenv("LOCAL_DATABASE_URL", raising=False)
    assert models.get_default_postgres_url() == LOCAL
    assert ":5433" not in models.get_default_postgres_url()


def test_launchers_share_one_resolution_library():
    """scripts/dev and scripts/start must not fork the precedence rules."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    lib = root / "scripts" / "lib" / "db_target.sh"
    assert lib.exists(), "shared resolution library is missing"
    for launcher in ("dev", "start"):
        text = (root / "scripts" / launcher).read_text(encoding="utf-8")
        assert "scripts/lib/db_target.sh" in text
        assert "resolve_db_target" in text


def test_manage_users_never_bootstraps_schema_on_shared(monkeypatch):
    """Provisioning an account must not create tables on the shared project.

    `scripts/manage_users.py` calls `create_all` so a fresh local database can
    accept its first admin; on the shared target that would be a schema change
    by side effect, which `db/schema.py` already refuses.
    """
    import importlib.util
    from pathlib import Path

    from db import models as db_models

    path = Path(__file__).resolve().parent.parent / "scripts" / "manage_users.py"
    spec = importlib.util.spec_from_file_location("manage_users_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []
    monkeypatch.setattr(
        db_models.Base.metadata, "create_all", lambda *a, **k: calls.append(1)
    )
    monkeypatch.setattr(db_target, "is_shared_host", lambda url: True)
    module._ensure_schema()

    assert calls == [], "create_all ran against the shared target"


def test_mirror_tool_refuses_to_push_upward():
    """The shared dataset is written by its owner; a bulk local→shared restore
    has no undo, so the tool must refuse it outright."""
    from pathlib import Path
    import subprocess

    script = Path(__file__).resolve().parent.parent / "scripts" / "mirror_db"
    assert script.exists()
    pushed = subprocess.run([str(script), "push"], capture_output=True, text=True)
    assert pushed.returncode == 1
    assert "deliberately" in pushed.stderr.lower()
