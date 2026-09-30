#!/usr/bin/env python3
"""Prune old analysis_results rows per the Phase 5 retention policy.

Keep the last N analyses per case (default 20); closure-linked cases are
never touched. Dry-run by default — pass --apply to delete.

Intended for cron/launchd (see scripts/local.soc-platform.backup.plist for
the sibling backup schedule) or a manual ops pass:

    .venv314/bin/python scripts/prune_analysis_results.py           # preview
    .venv314/bin/python scripts/prune_analysis_results.py --apply   # delete
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import SessionLocal  # noqa: E402
from services.retention_service import (  # noqa: E402
    DEFAULT_KEEP_LAST_N,
    prune_analysis_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP_LAST_N,
        help=f"analysis rows to keep per case (default {DEFAULT_KEEP_LAST_N})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete prunable rows (default is a dry run)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = prune_analysis_results(db, keep_last_n=args.keep, dry_run=not args.apply)
    finally:
        db.close()

    mode = "DRY RUN — nothing deleted" if summary["dry_run"] else "APPLIED"
    print(
        f"[{mode}] scanned={summary['scanned']} kept={summary['kept']} "
        f"prunable={summary['prunable']} deleted={summary['deleted']} "
        f"closure-protected cases={summary['protected_cases']} "
        f"keep_last_n={summary['keep_last_n']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
