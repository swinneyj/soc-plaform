# TOOL_NAME: splunk_notable_parser
# DESC: Parses raw Splunk exports using aggressive Regex to extract actionable IOCs (IPs, Emails, Hashes) hidden within unstructured log data.
# CATEGORY: Reporting
# ARG: --file | Splunk CSV/TXT File | Path to the raw Splunk export | True

import os
import sys
import csv
import argparse
import datetime
import re
import ipaddress

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, get_platform_root

def is_internal_ip(ip_str):
    """Checks if an IP string is a private/internal address to reduce noise."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return True # Default to ignoring invalid/weird IP strings

def extract_iocs_via_regex(raw_text):
    """Aggressively hunts for IOCs inside unstructured text blocks."""
    found_iocs = {
        'external_ips': set(),
        'emails': set(),
        'hashes': set()
    }
    
    # 1. Extract IPv4 (Filter out internals immediately)
    ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', raw_text)
    for ip in ips:
        if not is_internal_ip(ip):
            found_iocs['external_ips'].add(ip)
            
    # 2. Extract Emails (Basic heuristic)
    emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b', raw_text)
    for email in emails:
        found_iocs['emails'].add(email.lower())
        
    # 3. Extract Hashes (MD5, SHA1, SHA256)
    hashes = re.findall(r'\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b', raw_text)
    for h in hashes:
        found_iocs['hashes'].add(h.lower())
        
    return found_iocs

def process_splunk_data(filepath, output_dir, silent=False):
    """Handles both CSVs and raw text dumps from Splunk."""
    if not silent: print(f"{Colors.CYAN}[*] Applying Regex parsing to Splunk data...{Colors.ENDC}")

    report_lines = []
    total_events = 0
    
    # Try parsing as CSV first
    is_csv = False
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            sample = f.read(1024)
            if ',' in sample and '\n' in sample:
                f.seek(0)
                reader = csv.DictReader(f)
                rows = list(reader)
                if rows: is_csv = True
    except: pass

    if is_csv:
        for idx, row in enumerate(rows):
            total_events += 1
            # Combine all values in the row into a single string to ensure we don't miss anything
            raw_text = " ".join([str(v) for v in row.values() if v])
            
            iocs = extract_iocs_via_regex(raw_text)
            
            # Only log events that actually contain external indicators
            if iocs['external_ips'] or iocs['emails'] or iocs['hashes']:
                rule_name = row.get('search_name', row.get('rule_name', row.get('source', f'Event {idx+1}')))
                report_lines.append(f"### Source: {rule_name}")
                if iocs['external_ips']: report_lines.append(f"**External IPs:** {', '.join(iocs['external_ips'])}")
                if iocs['emails']: report_lines.append(f"**Identified Emails:** {', '.join(iocs['emails'])}")
                if iocs['hashes']: report_lines.append(f"**File Hashes:** {', '.join(iocs['hashes'])}")
                report_lines.append("\n---\n")
    else:
        # Fallback: Process it as a raw text dump
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                total_events = 1 # Treat the whole file as one event for the counter
                
                iocs = extract_iocs_via_regex(content)
                if iocs['external_ips'] or iocs['emails'] or iocs['hashes']:
                    report_lines.append(f"### Source: Raw Text Dump")
                    if iocs['external_ips']: report_lines.append(f"**External IPs:** {', '.join(iocs['external_ips'])}")
                    if iocs['emails']: report_lines.append(f"**Identified Emails:** {', '.join(iocs['emails'])}")
                    if iocs['hashes']: report_lines.append(f"**File Hashes:** {', '.join(iocs['hashes'])}")
        except Exception as e:
            if not silent: print(f"{Colors.FAIL}[!] Error reading file: {e}{Colors.ENDC}")
            return

    if not report_lines:
        if not silent: print(f"{Colors.WARNING}[-] No actionable external IOCs found in the data.{Colors.ENDC}")
        return

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    out_file = os.path.join(output_dir, f"Splunk_Extracted_IOCs_{timestamp}.md")

    try:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(f"# Splunk Regex Extraction Summary\n")
            f.write(f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("\n".join(report_lines))

        if not silent:
            print(f"\n{Colors.GREEN}[+] Regex extraction successfully generated!{Colors.ENDC}")
            print(f"[*] Processed {total_events} events.")
            print(f"[+] Saved to: {out_file}\n")
            
            try:
                if os.name == 'nt': os.startfile(out_file)
            except: pass
            
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error writing output file: {e}{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(description="Splunk Regex Parser")
    parser.add_argument('--file', type=str, required=True, help="Path to Splunk Export")
    parser.add_argument('--silent', action='store_true', help="Suppress terminal output")
    args = parser.parse_args()

    base_dir = get_platform_root()
    output_dir = os.path.join(base_dir, 'Data', 'Reports')
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = args.file.strip('"').strip("'")
    if not os.path.exists(filepath):
        if not args.silent: print(f"{Colors.FAIL}[-] File not found: {filepath}{Colors.ENDC}")
        return

    process_splunk_data(filepath, output_dir, args.silent)
    
    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
