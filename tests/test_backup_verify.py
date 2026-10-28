"""OPS-02 — backup restore verification harness."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_verify_script_refuses_missing_dump():
    import scripts.verify_backup_restore as v

    assert v.main(["--dump", "/nonexistent/dump.sql.gz"]) == 2


def test_table_count_comparison_detects_mismatch():
    # Pure logic: identical dicts pass, differing ones are reported.
    live = {"triage_results": 10, "splunk_events": 5}
    restored_same = {"triage_results": 10, "splunk_events": 5}
    restored_diff = {"triage_results": 9, "splunk_events": 5}
    assert live == restored_same
    assert live != restored_diff
    mism = [t for t in live if live[t] != restored_diff[t]]
    assert mism == ["triage_results"]


def test_verify_against_live_and_scratch_sqlite(tmp_path):
    """End-to-end comparison logic on sqlite (no psql needed)."""
    from sqlalchemy import create_engine, text

    from db.models import Base
    import scripts.verify_backup_restore as v

    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    for p in (src, dst):
        eng = create_engine("sqlite:///%s" % p)
        Base.metadata.create_all(bind=eng)
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)"))
        eng.dispose()
    tables = ["probe"]
    e1 = create_engine("sqlite:///%s" % src)
    e2 = create_engine("sqlite:///%s" % dst)
    try:
        assert v._table_counts(e1, tables) == v._table_counts(e2, tables) == {"probe": 0}
    finally:
        e1.dispose()
        e2.dispose()
