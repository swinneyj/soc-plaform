"""SEC-05 — unmounted legacy ("attic") endpoints cannot be accidentally mounted.

- Every APIRouter under api/routes/* must be included by api.main (ui serves
  static via mount, not include_router — explicitly allowed).
- scripts/attic/db_routes.py is dead code: it must never be imported by the
  app, and it carries no require_role/require_api_key guards (pre-auth-scaffold).
- api/helpers must contain no route decorators.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_all_api_routers_are_mounted():
    main_text = (ROOT / "api" / "main.py").read_text()
    for mod in (ROOT / "api" / "routes").glob("*.py"):
        if mod.name in ("__init__.py", "ui.py"):
            continue
        # router modules expose `router = APIRouter()` and main includes it.
        text = mod.read_text()
        assert "router = APIRouter()" in text, mod.name
        # main imports it and includes it (auth uses `auth_router`).
        assert (f"_{mod.stem}_router" in main_text or f"{mod.stem}_router" in main_text), f"{mod.name} not included in api/main.py"


def test_attic_is_not_imported_and_has_no_auth_guards():
    main_text = (ROOT / "api" / "main.py").read_text()
    assert "attic" not in main_text.lower(), "app must never import scripts/attic"
    attic = ROOT / "scripts" / "attic" / "db_routes.py"
    assert attic.exists(), "attic inventory anchor missing"
    text = attic.read_text()
    # Pre-auth-scaffold: no guard dependencies — documents WHY it must stay unmounted.
    assert "Depends(require_api_key" not in text and "Depends(require_role" not in text
    # Explicit test-only gate: importing it without opt-in fails closed.
    assert 'SOC_ENABLE_ATTIC' in text, "attic must carry an explicit opt-in flag"


def test_helpers_contain_no_routes():
    for mod in (ROOT / "api" / "helpers").glob("*.py"):
        text = mod.read_text()
        assert "@router." not in text, f"route decorator in helpers/{mod.name}"
        assert "APIRouter()" not in text, f"router in helpers/{mod.name}"
