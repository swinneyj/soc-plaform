#!/usr/bin/env python3
# TOOL_NAME: splunk_folder_watcher
# DESC: Watches a folder for Splunk CSV exports, ingests them via the Splunk boundary, and archives processed files.
# CATEGORY: Uncategorized
"""
Splunk CSV Folder Watcher
Monitors a folder for new Splunk CSV exports and auto-ingests them.

All ingestion flows through the Splunk boundary ("the latch"): each file is
validated, quarantined under a batch id, then ingested — auditable and
purgable later (see services/splunk_boundary.py).
"""

import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

# Ensure the platform root (e.g., /app) is on sys.path so the services
# package is importable when running inside the container.
platform_root = get_platform_root()
if platform_root not in sys.path:
    sys.path.insert(0, platform_root)


def ingest_splunk_csv(csv_file: str, silent: bool = False) -> dict:
    """Ingest a Splunk CSV export through the boundary.

    Returns the boundary's ingest stats plus batch_id. Kept under this name
    so watch_folder and any external callers behave exactly as before.
    """
    from services.splunk_boundary import admit_file

    if not os.path.exists(csv_file):
        return {"success": False, "error": f"File not found: {csv_file}", "rows": 0}

    try:
        result = admit_file(csv_file, source_label="cli:splunk_folder_watcher")
    except ValueError as exc:
        if not silent:
            print(f"{Colors.FAIL}[!] Boundary rejected {os.path.basename(csv_file)}: {exc}{Colors.ENDC}")
        return {"success": False, "error": str(exc), "rows": 0}
    except FileNotFoundError as exc:
        if not silent:
            print(f"{Colors.FAIL}[!] {exc}{Colors.ENDC}")
        return {"success": False, "error": str(exc), "rows": 0}

    stats = result["ingest"]
    stats["batch_id"] = result["manifest"]["batch_id"]

    if not silent and stats.get("success"):
        print(f"{Colors.GREEN}[+] Ingestion complete!{Colors.ENDC}")
        print(f"    Batch: {stats['batch_id']} | Inserted: {stats['rows_inserted']} | "
              f"Skipped: {stats['rows_skipped']} | Errors: {len(stats['errors'])}")

    return stats

def watch_folder(watch_dir: str, archive_dir: str = None, silent: bool = False):
    """Watch a folder for new CSV files and auto-ingest them."""
    if not os.path.exists(watch_dir):
        print(f"{Colors.FAIL}[!] Watch directory not found: {watch_dir}{Colors.ENDC}")
        return
    
    if archive_dir is None:
        archive_dir = os.path.join(watch_dir, 'archived')
    
    os.makedirs(archive_dir, exist_ok=True)
    
    if not silent:
        print(f"{Colors.CYAN}[*] Watching folder: {watch_dir}{Colors.ENDC}")
        print(f"{Colors.CYAN}[*] Archive folder: {archive_dir}{Colors.ENDC}\n")
    
    # Find all CSV files in the directory
    csv_files = list(Path(watch_dir).glob('*.csv'))
    
    if not csv_files:
        if not silent:
            print(f"{Colors.WARNING}[-] No CSV files found in {watch_dir}{Colors.ENDC}")
        return
    
    for csv_file in csv_files:
        if not silent:
            print(f"{Colors.CYAN}[*] Processing: {csv_file.name}{Colors.ENDC}")
        
        result = ingest_splunk_csv(str(csv_file), silent=silent)
        
        if result["success"]:
            # Move to archive
            archive_path = os.path.join(archive_dir, csv_file.name)
            shutil.move(str(csv_file), archive_path)
            
            if not silent:
                print(f"{Colors.GREEN}[+] Archived to: {archive_path}{Colors.ENDC}\n")
        else:
            if not silent:
                print(f"{Colors.FAIL}[!] Error: {result['error']}{Colors.ENDC}\n")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Watch folder for Splunk CSV exports and auto-ingest")
    parser.add_argument('--watch-dir', type=str, help='Directory to watch for CSV files')
    parser.add_argument('--archive-dir', type=str, help='Directory to move processed files (default: watch-dir/archived)')
    parser.add_argument('--silent', action='store_true', help='Suppress output')
    
    args = parser.parse_args()
    
    # Default watch directory
    if args.watch_dir:
        watch_dir = args.watch_dir
    else:
        platform_root = get_platform_root()
        watch_dir = os.path.join(platform_root, 'Splunk_Exports')
        os.makedirs(watch_dir, exist_ok=True)
    
    watch_folder(watch_dir, args.archive_dir, args.silent)

if __name__ == "__main__":
    main()
