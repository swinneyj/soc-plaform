"""SEC-02 — the route/role matrix must describe the app that exists.

A route added without regenerating docs/ROUTE_ROLE_MATRIX.md would otherwise
slip outside the reviewed policy silently: the matrix is the artifact the owner
approves, so it fails closed here instead.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_route_role_matrix_is_current():
    proc = subprocess.run(
        [sys.executable, "scripts/route_matrix.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "docs/ROUTE_ROLE_MATRIX.md is stale — regenerate with "
        "`python scripts/route_matrix.py` and re-insert the table.\n"
        + proc.stdout
        + proc.stderr
    )


def test_matrix_cross_checks_against_openapi():
    """The inventory is complete: served method+path pairs equal listed ones."""
    sys.path.insert(0, ROOT)
    import scripts.route_matrix as matrix  # noqa: E402

    served, listed, diff = matrix.verify_coverage(matrix._route_objects())
    assert served == listed == 74, (served, listed)
    assert diff == [], diff
