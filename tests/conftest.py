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
