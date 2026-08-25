#!/usr/bin/env python3
"""
Splunk CSV Folder Watcher
Monitors a folder for new Splunk CSV exports and auto-ingests them.
"""

import os
import sys
import shutil
import csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

# Ensure the platform root (e.g., /app) is on sys.path so the db package
# is importable when running inside the container.
platform_root = get_platform_root()
if platform_root not in sys.path:
    sys.path.insert(0, platform_root)

def ingest_splunk_csv(csv_file: str, silent: bool = False) -> dict:
    """Ingest a Splunk CSV export into the database."""
    if not os.path.exists(csv_file):
        return {"success": False, "error": f"File not found: {csv_file}", "rows": 0}
    
    try:
        from db.models import SessionLocal, SplunkEvent
        
        db = SessionLocal()
        stats = {
            "success": True,
            "rows_read": 0,
            "rows_inserted": 0,
            "rows_skipped": 0,
            "errors": [],
            "file": os.path.basename(csv_file)
        }
        
        with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
            csv_reader = csv.DictReader(f)
            
            if not csv_reader.fieldnames:
                return {"success": False, "error": "CSV is empty", "rows": 0}
            
            for row_num, row in enumerate(csv_reader, start=2):
                stats["rows_read"] += 1
                
                try:
                    # Map Splunk fields (case-insensitive)
                    field_lower = {k.lower(): v for k, v in row.items()}
                    
                    sourcetype = field_lower.get('sourcetype') or field_lower.get('source::type') or 'splunk:notable'
                    source = field_lower.get('source') or field_lower.get('_source') or 'splunk_export'
                    host = field_lower.get('host') or field_lower.get('_host') or 'unknown'
                    raw = field_lower.get('_raw') or field_lower.get('raw') or str(row)
                    
                    # Parse timestamp
                    timestamp_str = field_lower.get('_time') or field_lower.get('time') or field_lower.get('timestamp')
                    try:
                        if timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            timestamp = datetime.utcnow()
                    except:
                        timestamp = datetime.utcnow()
                    
                    # Check for duplicates
                    existing = db.query(SplunkEvent).filter(
                        SplunkEvent.sourcetype == sourcetype,
                        SplunkEvent.source == source,
                        SplunkEvent.host == host,
                        SplunkEvent.timestamp == timestamp
                    ).first()
                    
                    if existing:
                        stats["rows_skipped"] += 1
                        continue
                    
                    event = SplunkEvent(
                        sourcetype=sourcetype,
                        source=source,
                        host=host,
                        raw=raw[:2000],
                        timestamp=timestamp
                    )
                    db.add(event)
                    stats["rows_inserted"] += 1
                    
                except Exception as e:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {row_num}: {str(e)[:100]}")
                
                if stats["rows_inserted"] % 50 == 0 and stats["rows_inserted"] > 0:
                    db.commit()
            
            db.commit()
        
        db.close()
        
        if not silent:
            print(f"{Colors.GREEN}[+] Ingestion complete!{Colors.ENDC}")
            print(f"    Inserted: {stats['rows_inserted']} | Skipped: {stats['rows_skipped']} | Errors: {len(stats['errors'])}")
        
        return stats
    
    except Exception as e:
        return {"success": False, "error": str(e), "rows": 0}

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
