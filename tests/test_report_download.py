"""Regression tests for the /api/reports/{report_name} path-traversal fix.

download_report must never serve files outside the platform Reports
directory, regardless of `..` segments, absolute paths, or symlinks.
"""

import os

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from core_lib.utils import get_reports_dir


@pytest.fixture()
def client():
    return TestClient(api_main.app)


@pytest.fixture()
def report_on_disk():
    reports_dir = get_reports_dir()
    os.makedirs(reports_dir, exist_ok=True)
    name = "regression_probe_report.txt"
    path = os.path.join(reports_dir, name)
    with open(path, "w") as fh:
        fh.write("regression probe\n")
    yield name
    os.remove(path)


def test_legit_report_downloads(client, report_on_disk):
    resp = client.get(f"/api/reports/{report_on_disk}")
    assert resp.status_code == 200
    assert "regression probe" in resp.text


def test_dotdot_escape_rejected(client):
    # %2F decodes to "/" inside the path parameter; ../.. must not escape.
    resp = client.get("/api/reports/..%2F..%2F.env")
    assert resp.status_code == 404


def test_absolute_path_rejected(client):
    # os.path.join(base, "/etc/passwd") used to return /etc/passwd outright;
    # the resolved candidate must stay inside the reports root.
    resp = client.get("/api/reports/%2Fetc%2Fpasswd")
    assert resp.status_code == 404


def test_nested_escape_rejected(client):
    resp = client.get("/api/reports/subdir%2F..%2F..%2F.env")
    assert resp.status_code == 404


def test_missing_report_is_404(client):
    resp = client.get("/api/reports/definitely-not-here.txt")
    assert resp.status_code == 404


def test_symlink_escape_rejected(client, tmp_path):
    reports_dir = get_reports_dir()
    os.makedirs(reports_dir, exist_ok=True)
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("should never be served")
    link = os.path.join(reports_dir, "regression_probe_symlink.txt")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(str(secret), link)
    try:
        resp = client.get("/api/reports/regression_probe_symlink.txt")
        assert resp.status_code == 404
    finally:
        os.remove(link)
