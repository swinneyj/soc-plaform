#!/usr/bin/env python3
"""
Splunk CSV Ingestion Tool
Parses Splunk exports (CSV) and loads events into SQLite database.
Handles common Splunk export formats with field deduplication and error handling.
"""

import csv
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

def ingest_splunk_csv(csv_file: str, silent: bool = False) -> dict:
    """
    Parse Splunk CSV and load into database.
    
    Args:
        csv_file: Path to Splunk CSV export
        silent: Suppress terminal output
    
    Returns:
        Dict with ingestion stats (rows_read, rows_inserted, errors)
    """
    if not silent:
        print(f"{Colors.CYAN}[*] Starting Splunk CSV ingestion...{Colors.ENDC}")
    
    if not os.path.exists(csv_file):
        error_msg = f"CSV file not found: {csv_file}"
        if not silent:
            print(f"{Colors.FAIL}[!] {error_msg}{Colors.ENDC}")
        return {"success": False, "error": error_msg, "rows_read": 0, "rows_inserted": 0}
    
    try:
        # Import database models - add app root to path
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if app_root not in sys.path:
            sys.path.insert(0, app_root)
        
        from db.models import SessionLocal, SplunkEvent
        
        db = SessionLocal()
        stats = {
            "success": True,
            "rows_read": 0,
            "rows_inserted": 0,
            "rows_skipped": 0,
            "errors": [],
            "file": csv_file
        }
        
        with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
            csv_reader = csv.DictReader(f)
            
            if not csv_reader.fieldnames:
                error_msg = "CSV file is empty or malformed"
                if not silent:
                    print(f"{Colors.FAIL}[!] {error_msg}{Colors.ENDC}")
                return {"success": False, "error": error_msg, "rows_read": 0, "rows_inserted": 0}
            
            if not silent:
                print(f"{Colors.CYAN}[*] Detected fields: {', '.join(csv_reader.fieldnames)}{Colors.ENDC}")
            
            for row_num, row in enumerate(csv_reader, start=2):  # start=2 to account for header
                stats["rows_read"] += 1
                
                try:
                    # Extract common Splunk fields
                    sourcetype = row.get('sourcetype') or row.get('source::type') or 'unknown'
                    source = row.get('source') or row.get('_source') or 'unknown'
                    host = row.get('host') or row.get('_host') or 'unknown'
                    raw = row.get('_raw') or row.get('raw') or str(row)
                    
                    # Try to parse timestamp
                    timestamp_str = row.get('_time') or row.get('time') or row.get('timestamp')
                    try:
                        if timestamp_str:
                            # Handle common Splunk timestamp formats
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            timestamp = datetime.utcnow()
                    except:
                        timestamp = datetime.utcnow()
                    
                    # Check for duplicates (by sourcetype, source, host, timestamp)
                    existing = db.query(SplunkEvent).filter(
                        SplunkEvent.sourcetype == sourcetype,
                        SplunkEvent.source == source,
                        SplunkEvent.host == host,
                        SplunkEvent.timestamp == timestamp
                    ).first()
                    
                    if existing:
                        stats["rows_skipped"] += 1
                        continue
                    
                    # Create event
                    event = SplunkEvent(
                        sourcetype=sourcetype,
                        source=source,
                        host=host,
                        raw=raw[:2000],  # Limit raw event to 2000 chars
                        timestamp=timestamp
                    )
                    db.add(event)
                    stats["rows_inserted"] += 1
                    
                except Exception as e:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {row_num}: {str(e)[:100]}")
                
                # Commit every 100 rows
                if stats["rows_inserted"] % 100 == 0 and stats["rows_inserted"] > 0:
                    db.commit()
                    if not silent:
                        print(f"{Colors.GREEN}[+] Inserted {stats['rows_inserted']} events...{Colors.ENDC}")
            
            # Final commit
            db.commit()
        
        db.close()
        
        if not silent:
            print(f"\n{Colors.GREEN}[+] Ingestion complete!{Colors.ENDC}")
            print(f"{Colors.CYAN}[*] Summary:{Colors.ENDC}")
            print(f"    Rows read: {stats['rows_read']}")
            print(f"    Rows inserted: {stats['rows_inserted']}")
            print(f"    Rows skipped (duplicates): {stats['rows_skipped']}")
            if stats['errors']:
                print(f"    Errors: {len(stats['errors'])}")
        
        return stats
    
    except ImportError as e:
        error_msg = f"Database import failed: {str(e)}"
        if not silent:
            print(f"{Colors.FAIL}[!] {error_msg}{Colors.ENDC}")
        return {"success": False, "error": error_msg, "rows_read": 0, "rows_inserted": 0}
    except Exception as e:
        error_msg = f"Ingestion failed: {str(e)}"
        if not silent:
            print(f"{Colors.FAIL}[!] {error_msg}{Colors.ENDC}")
        return {"success": False, "error": error_msg, "rows_read": 0, "rows_inserted": 0}

def main():
    parser = argparse.ArgumentParser(
        description="Ingest Splunk CSV exports into SOC Platform SQLite database"
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
