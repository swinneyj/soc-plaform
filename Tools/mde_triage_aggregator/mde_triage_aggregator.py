# TOOL_NAME: mde_triage_aggregator
# DESC: Designed EXCLUSIVELY for raw Microsoft Defender (MDE) CSV exports. Use this to deduplicate incident storms, prune false positive noise, and format High-Severity alerts for shift handoff.
# CATEGORY: Reporting
# ARG: --file | CSV File | Path to the raw MDE Incident or Alert CSV export | True

import os
import csv
import argparse
import datetime
from collections import defaultdict

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

def print_use_cases():
    """Displays the explicit operational use cases for this tool."""
    print(f"\n{Colors.CYAN}{Colors.BOLD}" + "="*80)
    print(" OPERATIONAL USE CASES: MDE TRIAGE AGGREGATOR ".center(80))
    print("="*80 + f"{Colors.ENDC}")
    print(f"\n{Colors.WARNING}IMPORTANT:{Colors.ENDC} This tool only accepts raw CSV exports directly from Microsoft Defender for Endpoint (MDE).")
    
    print(f"\n{Colors.GREEN}[Use Case 1] Incident Storms (Alert Fatigue){Colors.ENDC}")
    print("   - Problem: A widespread campaign generated 100+ separate incident rows.")
    print("   - Solution: This tool deduplicates the rows, aggregates the impacted devices/accounts,")
    print("               and outputs a single line summarizing the total active alerts.")
    
    print(f"\n{Colors.GREEN}[Use Case 2] Daily Shift Handoff{Colors.ENDC}")
    print("   - Problem: The raw MDE CSV has 40+ columns of unreadable metadata.")
    print("   - Solution: It filters for 'High' severity, strips junk columns, and generates a TSV.")
    print("               You can copy/paste the TSV directly into Teams or Excel as a clean table.")
    
    print(f"\n{Colors.GREEN}[Use Case 3] False Positive Pruning{Colors.ENDC}")
    print("   - Problem: The queue is cluttered with closed/benign alerts.")
    print("   - Solution: Automatically drops any row classified as 'FalsePositive' or 'Clean',")
    print("               reporting the total 'noise' count while keeping the final output actionable.\n")

def process_mde_csv(filepath, output_dir, silent=False):
    filename = os.path.basename(filepath).lower()
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error reading file: {e}{Colors.ENDC}")
        return

    if not rows:
        if not silent: print(f"{Colors.FAIL}[-] CSV is empty or unreadable.{Colors.ENDC}")
        return

    is_incident = 'Incident name' in rows[0]
    
    summary = defaultdict(lambda: {
        'count': 0, 'categories': set(), 'devices': set(), 'accounts': set(),
        'service_sources': set(), 'determinations': set(), 'classifications': set(),
        'active_alerts': 0, 'statuses': set(), 'severity': set(), 'first_activity': []
    })

    noise_count = 0
    in_scope_count = 0

    if not silent: print(f"{Colors.CYAN}[*] Parsing MDE Export data...{Colors.ENDC}")

    for row in rows:
        if row.get('Severity', '') not in ['High']:
            continue

        classification = row.get('Classification', '')
        determination = row.get('Determination', '')
        
        if is_incident:
            if determination == 'False alert' and classification in ['Insufficient data', 'Clean']:
                noise_count += 1
                continue
        else:
            if classification == 'FalsePositive' and determination in ['Not malicious', 'Not enough data to validate']:
                noise_count += 1
                continue
                
        in_scope_count += 1
        key_name = row.get('Incident name') if is_incident else row.get('Alert name')
        
        impacted = row.get('Impacted assets', '')
        devices, accounts = [], []
        if 'Devices:' in impacted:
            d_part = impacted.split('Devices:')[1].split('Accounts:')[0]
            devices = [d.strip() for d in d_part.split(',') if d.strip()]
        if 'Accounts:' in impacted:
            a_part = impacted.split('Accounts:')[1]
            accounts = [a.strip() for a in a_part.split(',') if a.strip()]

        group = summary[key_name]
        group['count'] += 1
        group['severity'].add(row.get('Severity', ''))
        group['statuses'].add(row.get('Status', ''))
        group['determinations'].add(determination if determination else 'Not set')
        group['classifications'].add(classification if classification else 'Not set')
        
        if row.get('Categories') or row.get('Category'):
            group['categories'].add(row.get('Categories', row.get('Category', '')))
        if row.get('Service sources'):
            group['service_sources'].add(row.get('Service sources', ''))
            
        group['devices'].update(devices)
        group['accounts'].update(accounts)
        
        if is_incident:
            try: group['active_alerts'] += int(row.get('Active alerts', 0))
            except ValueError: pass
        else:
            if row.get('First activity'):
                group['first_activity'].append(row.get('First activity'))

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    prefix = "Incident" if is_incident else "Alert"
    out_file = os.path.join(output_dir, f"MDE_{prefix}_Triage_Summary_{timestamp}.tsv")

    try:
        with open(out_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            
            if is_incident:
                writer.writerow(['Incident Name', 'Severity', 'Categories', 'Impacted Devices', 'Impacted Accounts', 'Active Alerts', 'Status', 'Service Sources', 'Determination', 'Classification'])
            else:
                writer.writerow(['Alert name', 'Severity', 'Category', 'Classification', 'Impacted Devices', 'Impacted Accounts', 'Status', 'First activity', 'Determination'])

            for name, data in summary.items():
                display_name = f"{name} (x{data['count']})" if data['count'] > 1 else name
                devs = ", ".join(data['devices'])
                accs = ", ".join(data['accounts'])
                cats = ", ".join(data['categories'])
                sevs = ", ".join(data['severity'])
                stats = ", ".join(data['statuses'])
                dets = ", ".join(data['determinations'])
                classifs = ", ".join(data['classifications'])
                
                if is_incident:
                    alerts_str = f"{data['active_alerts']} (total across {data['count']})" if data['count'] > 1 else str(data['active_alerts'])
                    srcs = ", ".join(data['service_sources'])
                    writer.writerow([display_name, sevs, cats, devs, accs, alerts_str, stats, srcs, dets, classifs])
                else:
                    first_act = f"{min(data['first_activity'])} to {max(data['first_activity'])}" if data['first_activity'] else ""
                    writer.writerow([display_name, sevs, cats, classifs, devs, accs, stats, first_act, dets])

        if not silent:
            print(f"\n{Colors.GREEN}[+] MDE Triage Summary successfully generated!{Colors.ENDC}")
            print(f"[*] In-scope High Severity items: {in_scope_count}")
            print(f"[*] Excluded as noise: {noise_count}")
            print(f"[+] Saved to: {out_file}\n")
            print(f"{Colors.CYAN}[i] You can open this .tsv file with Excel, or copy its contents directly into a report/Teams.{Colors.ENDC}")
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error writing output file: {e}{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(description="MDE Triage Aggregator", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--file', type=str, help="Path to MDE CSV Export")
    parser.add_argument('--use-cases', action='store_true', help="Display the operational use cases for this tool")
    args = parser.parse_args()

    if args.use_cases:
        print_use_cases()
        return

    if not args.file:
        print_use_cases()
        print(f"\n{Colors.FAIL}[!] Error: You must provide a file to process using the --file argument.{Colors.ENDC}")
        return

    base_dir = get_platform_root()
    output_dir = os.path.join(base_dir, 'Data', 'Reports')
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = args.file.strip('"').strip("'")
    if not os.path.exists(filepath):
        print(f"{Colors.FAIL}[-] File not found: {filepath}{Colors.ENDC}")
        return

    process_mde_csv(filepath, output_dir)
    
    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
