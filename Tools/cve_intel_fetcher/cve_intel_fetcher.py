# TOOL_NAME: cve_intel_fetcher
# DESC: Instantly queries the NIST NVD database for a specific CVE to return its CVSS score, summary, and exploit status.
# CATEGORY: Intel Collection
# ARG: --cve | CVE ID | The specific CVE identifier to query (e.g., CVE-2023-12345) | True

import os
import sys
import json
import urllib.request
import urllib.error
import argparse
import re

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, get_platform_root

def fetch_cve_data(cve_id, silent=False):
    """Fetches and parses CVE data from the NIST NVD API."""
    
    # Clean and validate the input
    cve_id = cve_id.strip().upper()
    if not re.match(r'^CVE-\d{4}-\d{4,}$', cve_id):
        if not silent: print(f"{Colors.FAIL}[!] Invalid format. Expected format: CVE-YYYY-NNNNN{Colors.ENDC}")
        return

    if not silent: print(f"{Colors.CYAN}[*] Querying NIST NVD for {cve_id}...{Colors.ENDC}")
    
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
    req = urllib.request.Request(url, headers={'User-Agent': 'SOC-Commander-IL5/1.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            
        vulnerabilities = data.get('vulnerabilities', [])
        
        if not vulnerabilities:
            if not silent: print(f"{Colors.WARNING}[-] No data found for {cve_id} in the NVD database.{Colors.ENDC}")
            return
            
        vuln_data = vulnerabilities[0].get('cve', {})
        
        # Extract Summary
        descriptions = vuln_data.get('descriptions', [])
        summary = "No English description available."
        for desc in descriptions:
            if desc.get('lang') == 'en':
                summary = desc.get('value')
                break
                
        # Extract CVSS Metrics (prefer v3.1, fallback to v3.0 or v2)
        metrics = vuln_data.get('metrics', {})
        cvss_data = None
        cvss_version = "Unknown"
        
        if 'cvssMetricV31' in metrics:
            cvss_data = metrics['cvssMetricV31'][0].get('cvssData', {})
            cvss_version = "3.1"
        elif 'cvssMetricV30' in metrics:
            cvss_data = metrics['cvssMetricV30'][0].get('cvssData', {})
            cvss_version = "3.0"
        elif 'cvssMetricV2' in metrics:
            cvss_data = metrics['cvssMetricV2'][0].get('cvssData', {})
            cvss_version = "2.0"

        # Format output
        if not silent:
            print(f"\n{Colors.CYAN}{Colors.BOLD}" + "="*80)
            print(f" VULNERABILITY INTELLIGENCE: {cve_id} ".center(80))
            print("="*80 + f"{Colors.ENDC}")
            
            # Print Severity/Score
            if cvss_data:
                score = cvss_data.get('baseScore', 'N/A')
                severity = cvss_data.get('baseSeverity', 'UNKNOWN')
                
                # Color code severity
                sev_color = Colors.GREEN
                if severity == 'CRITICAL' or severity == 'HIGH': sev_color = Colors.FAIL
                elif severity == 'MEDIUM': sev_color = Colors.WARNING
                
                print(f"{Colors.BOLD}CVSS {cvss_version} Score:{Colors.ENDC} {score} [{sev_color}{severity}{Colors.ENDC}]")
                print(f"{Colors.BOLD}Vector:{Colors.ENDC} {cvss_data.get('vectorString', 'N/A')}")
            else:
                print(f"{Colors.WARNING}CVSS Score: Pending analysis in NVD.{Colors.ENDC}")
                
            print(f"\n{Colors.BOLD}Summary:{Colors.ENDC}\n{summary}")
            
            # Print Exploit Status (CISA KEV check via NVD tags)
            cisa_kev = vuln_data.get('cisaExploitAdd', None)
            if cisa_kev:
                print(f"\n{Colors.FAIL}{Colors.BOLD}[!] EXPLOIT WARNING: This vulnerability is on the CISA KEV list.{Colors.ENDC}")
                print(f"    Added: {cisa_kev}")
                print(f"    Required Action: {vuln_data.get('cisaRequiredAction', 'Patch immediately')}")
            
            print(f"\n{Colors.BLUE}Reference: https://nvd.nist.gov/vuln/detail/{cve_id}{Colors.ENDC}")
            print(f"{Colors.CYAN}" + "="*80 + f"{Colors.ENDC}")

    except urllib.error.HTTPError as e:
        if e.code == 403 or e.code == 503:
            if not silent: print(f"{Colors.FAIL}[!] API Rate Limit Exceeded or Blocked. NIST may be throttling requests.{Colors.ENDC}")
        else:
            if not silent: print(f"{Colors.FAIL}[!] HTTP Error: {e.code} - {e.reason}{Colors.ENDC}")
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error fetching data: {e}{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(description="CVE Intelligence Fetcher")
    parser.add_argument('--cve', type=str, required=True, help="The CVE identifier (e.g., CVE-2023-12345)")
    parser.add_argument('--silent', action='store_true', help="Suppress terminal output")
    args = parser.parse_args()

    fetch_cve_data(args.cve, args.silent)
    
    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
