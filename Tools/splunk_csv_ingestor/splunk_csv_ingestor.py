#!/usr/bin/env python3
# TOOL_NAME: splunk_csv_ingestor
# DESC: Parses Splunk CSV exports and loads events into the active database backend.
# CATEGORY: Uncategorized
"""
Splunk CSV Ingestion Tool
Parses Splunk exports (CSV) and loads events into the active database backend.

All ingestion flows through the Splunk boundary ("the latch"): the file is
validated, quarantined under a batch id, then ingested — so every batch is
auditable and purgable later (see services/splunk_boundary.py).
"""

import argparse
import os
import sys

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

# Ensure the platform root is importable for the services package
platform_root = get_platform_root()
if platform_root not in sys.path:
    sys.path.insert(0, platform_root)


def ingest_splunk_csv(csv_file: str, silent: bool = False) -> dict:
    """
    Ingest a Splunk CSV export through the Splunk boundary.

    Returns:
        Dict with ingestion stats (rows_read, rows_inserted, errors) plus
        batch_id when the boundary accepted the file.
    """
    from services.splunk_boundary import admit_file

    try:
        result = admit_file(csv_file, source_label="cli:splunk_csv_ingestor")
    except ValueError as exc:
        # Boundary refused (validation failed) — surface a clear operator error.
        if not silent:
            print(f"{Colors.FAIL}[!] Boundary rejected file: {exc}{Colors.ENDC}")
        return {"success": False, "error": str(exc), "rows_read": 0, "rows_inserted": 0}
    except FileNotFoundError as exc:
        if not silent:
            print(f"{Colors.FAIL}[!] {exc}{Colors.ENDC}")
        return {"success": False, "error": str(exc), "rows_read": 0, "rows_inserted": 0}

    stats = result["ingest"]
    manifest = result["manifest"]
    stats["batch_id"] = manifest["batch_id"]

    if not silent:
        if stats.get("success"):
            print(f"{Colors.GREEN}[+] Ingestion complete!{Colors.ENDC}")
            print(f"{Colors.CYAN}[*] Summary:{Colors.ENDC}")
            print(f"    Batch: {manifest['batch_id']}")
            print(f"    Rows read: {stats['rows_read']}")
            print(f"    Rows inserted: {stats['rows_inserted']}")
            print(f"    Rows skipped (duplicates): {stats['rows_skipped']}")
            if stats["errors"]:
                print(f"    Errors: {len(stats['errors'])}")
        else:
            print(f"{Colors.FAIL}[!] Ingestion failed: {stats.get('error', 'Unknown error')}{Colors.ENDC}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Splunk CSV exports into the SOC Platform via the Splunk boundary"
    )
    parser.add_argument(
        '--target',
        required=True,
        help="Path to Splunk CSV export file"
    )
    parser.add_argument(
        '--silent',
        action='store_true',
        help="Suppress terminal output"
    )

    args = parser.parse_args()

    # Strip quotes if dragged from Windows Explorer
    target = args.target.strip('"').strip("'")

    result = ingest_splunk_csv(target, args.silent)

    if not args.silent:
        if result['success']:
            print(f"\n{Colors.GREEN}✓ Ingestion successful{Colors.ENDC}")
            input("\nPress Enter to return to Commander...")
        else:
            print(f"\n{Colors.FAIL}✗ Ingestion failed: {result.get('error', 'Unknown error')}{Colors.ENDC}")
            input("\nPress Enter to return to Commander...")


if __name__ == "__main__":
    main()
