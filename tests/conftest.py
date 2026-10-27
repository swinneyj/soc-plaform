"""Shared pytest configuration and fixtures.

CORS middleware is mounted at api.main import time only when CORS_ORIGINS is
set. conftest.py is imported by pytest before any test module, so setting it
here guarantees the middleware exists regardless of test import order.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

os.environ.setdefault("CORS_ORIGINS", "https://test.local")


@pytest.fixture(autouse=True)
def _auto_isolated_db(monkeypatch):
    """Hermetic DB for every test (OPS-01 follow-up).

    The suite contract is CI never touches Postgres/Neon: several suites
    build a plain TestClient without requesting db_client, which was fine
    while /api/health was static but broke when OPS-01 made it probe the
    real database (9 failures in CI, green locally where Postgres runs).
    This autouse fixture repoints db.models.SessionLocal at a fresh
    in-memory SQLite database (tables created) so any route using the
    platform convention `from db.models import SessionLocal` inside its
    handler stays hermetic. Tests that set their own SessionLocal (db_client,
    api_client, boom/None guards) simply re-patch over this — last
    monkeypatch wins, teardown restores in reverse.
    """
    import db.models as db_models
    import api.main as api_main

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    try:
        api_main._RATE_HITS.clear()
    except Exception:
        pass
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", test_session)
    try:
        yield
    finally:
        engine.dispose()


@pytest.fixture()
def db_client(monkeypatch):
    """TestClient over an isolated in-memory SQLite database.

    No live Postgres and no Ollama: routes that only need persistence (paste,
    promote, search-one, evidence) run unchanged against this client. Use
    ``client.test_session()`` to read rows back through a fresh session, and
    ``client.fake_ollama`` is deliberately absent — tests that need a model
    patch ``get_ollama_client`` themselves.
    """
    import db.models as db_models
    import api.main as api_main
    from api.main import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    api_main._RATE_HITS.clear()  # C2.1.5 limiter state is module-global
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", test_session)

    client = TestClient(app)
    client.test_session = test_session
    try:
        yield client
    finally:
        engine.dispose()
