# TOOL_NAME: Threat_Intel_Fetcher
# DESC: Aggregates live intel from CISA KEV, CISA Advisories, and Abuse.ch into a single comprehensive daily bundle. Ready for playbook automation.
# CATEGORY: Intel Collection
# ARG: --compare_dir | Compare Directory | Directory containing Extracted IOC CSVs to cross-reference | False

import os
import json
import urllib.request
import urllib.error
import argparse
import datetime
import re
import csv
import glob
import subprocess
import shutil

def get_request(url):
    """Basic HTTP GET request using urllib with a standard User-Agent."""
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        return f"Error: {e}"

def fetch_cisa_kev(f):
    f.write("=== 1. CISA KEV (Known Exploited Vulnerabilities) ===\n")
    data = get_request("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    if data.startswith("Error"):
        f.write(f"Failed to fetch CISA KEV: {data}\n\n")
        return ""
    try:
        json_data = json.loads(data)
        vulnerabilities = json_data.get('vulnerabilities', [])
        recent_vulns = sorted(vulnerabilities, key=lambda x: x.get('dateAdded', ''), reverse=True)[:5]
        for v in recent_vulns:
            f.write(f"CVE: {v.get('cveID')} | Added: {v.get('dateAdded')}\n")
            f.write(f"Product: {v.get('vendorProject')} {v.get('product')}\n")
            f.write(f"Details: {v.get('shortDescription')}\n")
            f.write("-" * 50 + "\n")
        f.write("\n")
        return data
    except Exception as e:
        f.write(f"Error parsing CISA KEV: {e}\n\n")
        return ""

def fetch_cisa_advisories(f):
    f.write("=== 2. CISA Advisories (Recent Updates) ===\n")
    data = get_request("https://www.cisa.gov/cybersecurity-advisories/all.xml")
    if data.startswith("Error"):
        f.write(f"Failed to fetch CISA Advisories: {data}\n\n")
        return ""
    try:
        titles = re.findall(r'<title>(.*?)</title>', data)
        links = re.findall(r'<link>(.*?)</link>', data)
        for i in range(1, min(6, len(titles))):
            title = titles[i].replace('<![CDATA[', '').replace(']]>', '').strip()
            link = links[i].strip()
            f.write(f"- {title}\n  Ref: {link}\n\n")
        return data
    except Exception as e:
        f.write(f"Error parsing CISA Advisories: {e}\n\n")
        return ""

def fetch_abuse_urlhaus(f):
    f.write("=== 3. Abuse.ch URLhaus (Active Malware URLs) ===\n")
    data = get_request("https://urlhaus.abuse.ch/downloads/csv_recent/")
    if data.startswith("Error"):
        f.write(f"Failed to fetch URLhaus: {data}\n\n")
        return ""
    try:
        lines = [line for line in data.split('\n') if line and not line.startswith('#')]
        for line in lines[:10]:
            parts = line.split('","')
            if len(parts) > 3:
                url = parts[2].replace('"', '')
                status = parts[3].replace('"', '')
                f.write(f"[{status.upper()}] {url}\n")
        f.write("\n")
        return data
    except Exception as e:
        f.write(f"Error parsing URLhaus: {e}\n\n")
        return ""

def fetch_abuse_feodotracker(f):
    f.write("=== 4. Abuse.ch Feodo Tracker (Active Botnet C2s) ===\n")
    data = get_request("https://feodotracker.abuse.ch/downloads/ipblocklist.csv")
    if data.startswith("Error"):
        f.write(f"Failed to fetch Feodo Tracker: {data}\n\n")
        return ""
    try:
        lines = [line for line in data.split('\n') if line and not line.startswith('#')]
        for line in lines[:10]:
            parts = line.split(',')
            if len(parts) >= 3:
                ip, port, malware = parts[1].replace('"',''), parts[2].replace('"',''), parts[3].replace('"','')
                f.write(f"IP: {ip}:{port} | Malware: {malware}\n")
        f.write("\n")
        return data
    except Exception as e:
        f.write(f"Error parsing Feodo Tracker: {e}\n\n")
        return ""

def fetch_abuse_threatfox(f):
    f.write("=== 5. Abuse.ch ThreatFox (Recent IOCs) ===\n")
    data = get_request("https://threatfox.abuse.ch/export/csv/recent/")
    if data.startswith("Error"):
        f.write(f"Failed to fetch ThreatFox: {data}\n\n")
        return ""
    try:
        lines = [line for line in data.split('\n') if line and not line.startswith('#')]
        for line in lines[:10]:
            parts = line.split('","')
            if len(parts) >= 4:
                ioc_val, ioc_type = parts[2].replace('"',''), parts[3].replace('"','')
                f.write(f"Type: {ioc_type} | IOC: {ioc_val}\n")
        f.write("\n")
        return data
    except Exception as e:
        f.write(f"Error parsing ThreatFox: {e}\n\n")
        return ""

def compare_iocs_to_intel(ioc_dir, threat_corpus, f, silent=False):
    """Cross-references extracted IOCs exclusively against active threat feeds."""
    if not os.path.exists(ioc_dir):
        if not silent: print(f"  [-] Directory not found: {ioc_dir}")
        return
        
    list_of_files = glob.glob(os.path.join(ioc_dir, 'Extracted_IOCs_*.csv'))
    if not list_of_files:
        if not silent: print("  [-] No extracted IOC CSVs found for comparison.")
        return
        
    latest_csv = max(list_of_files, key=os.path.getctime)
    if not silent: print(f"  [*] Comparing IOCs from: {os.path.basename(latest_csv)}")
    
    f.write("\n" + "="*50 + "\n🔥 AUTOMATED IOC COMPARISON RESULTS 🔥\n" + "="*50 + "\n\n")
    match_found = False
    
    try:
        with open(latest_csv, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, None) # Skip header
            for row in reader:
                if len(row) < 2: continue
                ioc_type, val = row[0], row[1]
                
                # Re-fang for comparison
                raw_val = val.replace('[.]', '.').replace('[://]', '://').replace('hxxp', 'http')
                
                # Compare ONLY against the threat_corpus (Abuse.ch feeds)
                if raw_val.lower() in threat_corpus.lower():
                    f.write(f"[ALERT] Malicious Match Found!\n")
                    f.write(f" - Type:  {ioc_type}\n")
                    f.write(f" - Value: {val}\n")
                    f.write(f" - Note:  This indicator appeared in today's active Abuse.ch feeds.\n\n")
                    match_found = True
                    
        if not match_found:
            f.write("[OK] No extracted IOCs matched current live threat feeds.\n\n")
    except Exception as e:
        f.write(f"[!] Error reading IOC CSV for comparison: {e}\n\n")

def main():
    parser = argparse.ArgumentParser(description="Multi-Source Threat Intel Fetcher")
    parser.add_argument('--silent', action='store_true', help="Run without user prompts")
    parser.add_argument('--compare_dir', type=str, help="Directory containing Extracted IOC CSVs to cross-reference")
    args = parser.parse_args()

    if not args.silent:
        print("\n" + "="*60)
        print(" THREAT INTEL FETCHER & COMPARATOR ")
        print("="*60)
        print("[*] Reaching out to OSINT feeds. Please wait...")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir
        
    output_dir = os.path.join(platform_root, 'Data', 'Reports')
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    report_path = os.path.join(output_dir, f"SOC_Intel_Briefing_and_Triage_Results_{timestamp}.txt")
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"=== SOC INTELLIGENCE BUNDLE ===\n")
        f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 1. Fetch reference advisories (NOT added to comparator corpus)
        fetch_cisa_kev(f)
        fetch_cisa_advisories(f)
        
        # 2. Fetch threat intelligence (ADDED to comparator corpus)
        threat_corpus = ""
        threat_corpus += fetch_abuse_urlhaus(f)
        threat_corpus += fetch_abuse_feodotracker(f)
        threat_corpus += fetch_abuse_threatfox(f)
        
        # 3. Compare extracted IOCs strictly against the threat_corpus
        if args.compare_dir:
            compare_iocs_to_intel(args.compare_dir.strip('"').strip("'"), threat_corpus, f, args.silent)
            
    # Maintain a stable alias for the latest intel bundle
    latest_path = os.path.join(output_dir, 'SOC_Intel_Briefing_latest.txt')
    try:
        shutil.copy2(report_path, latest_path)
    except Exception:
        pass
    # --- AUTO-OPEN REPORT ---
    try:
        if os.name == 'nt':
            os.startfile(report_path)
        else:
            subprocess.run(['xdg-open', report_path])
        if not args.silent:
            print(f"  [+] Automatically opened report: {os.path.basename(report_path)}")
    except Exception as e:
        if not args.silent:
            print(f"  [!] Could not automatically open report: {e}")
        
    if not args.silent:
        print(f"[+] Scraping & Comparison complete. Briefing saved to:\n    {report_path}")
        if not os.environ.get("COMMANDER_BOOT"):
            input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    main()
