"""Single-point runtime providers for the API surface (the DI seam).

Routes call these through the ``deps`` module at request time instead of
importing core_lib/services bindings at module scope. Two consequences that
matter for tests and for drift:

* patching one attribute here (e.g. ``deps.get_platform_root``) redirects
  every route at once — no more per-module monkeypatch copies;
* the delegates resolve the underlying implementation lazily, so patching
  the underlying module (e.g. ``services.ollama_service.get_ollama_client``)
  keeps working exactly as before.

Future work: a ``get_db_session`` provider + FastAPI ``Depends`` wiring so
tests can inject DB fakes through ``app.dependency_overrides``.
"""
import os
import sys
from typing import Any

# Self-sufficient bootstrap: core_lib lives under Tools/ (main.py also puts
# it on the path at app import; this keeps api.deps importable standalone).
_tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from core_lib import utils as _core_utils  # noqa: E402


def get_platform_root() -> str:
    return _core_utils.get_platform_root()


def get_reports_dir() -> str:
    return _core_utils.get_reports_dir()


def get_archive_dir() -> str:
    return _core_utils.get_archive_dir()


def get_logs_dir() -> str:
    return _core_utils.get_logs_dir()


def get_ollama_client() -> Any:
    from services import ollama_service

    return ollama_service.get_ollama_client()
