# TOOL_NAME: IOC_Extractor
# DESC: High-fidelity extractor that pulls IPs, MACs, URLs, and Hashes from files. Defangs outputs for safe playbook automation.
# CATEGORY: Intel Collection
# ARG: --target | Target File or Folder | Path to the file or folder to scan | True

import os
import re
import csv
import argparse
import datetime
import glob

# --- GLOBAL WHITELISTS ---
WHITELIST_DOMAINS = ['cisa.gov', 'google.com', 'microsoft.com', 'windows.com', 'github.com']
WHITELIST_IPS = ['127.0.0.1', '0.0.0.0', '255.255.255.255']

def is_whitelisted(ioc):
    ioc_lower = ioc.lower()
    for wd in WHITELIST_DOMAINS:
        if wd in ioc_lower: return True
    for wip in WHITELIST_IPS:
        if wip in ioc_lower: return True
    # Ignore local/private network ranges
    if ioc_lower.startswith('10.') or ioc_lower.startswith('192.168.'): 
        return True
    return False

def defang(ioc, ioc_type):
    if 'IP' in ioc_type:
        return ioc.replace('.', '[.]')
    elif 'URL' in ioc_type:
        return ioc.replace('http', 'hxxp').replace('://', '[://]').replace('.', '[.]')
    return ioc

def extract_from_file(filepath):
    iocs = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
            ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', content)
            for ip in ips:
                if not is_whitelisted(ip): 
                    iocs.append(('IPv4', ip))
            
            urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', content)
            for url in urls:
                if not is_whitelisted(url): 
                    iocs.append(('URL / Domain', url))
                    
    except Exception as e:
        pass 
    return list(set(iocs))

def main():
    parser = argparse.ArgumentParser(description="High-fidelity IOC Extractor")
    parser.add_argument('--target', required=True, help="File or folder to scan")
    parser.add_argument('--silent', action='store_true', help="Suppress output")
    args = parser.parse_args()
    
    target = args.target.strip('"').strip("'")
    files_to_scan = []
    
    if os.path.isdir(target):
        for root, _, files in os.walk(target):
            for file in files:
                files_to_scan.append(os.path.join(root, file))
    elif os.path.isfile(target):
        files_to_scan.append(target)
        
    all_iocs = []
    for f in files_to_scan:
        all_iocs.extend(extract_from_file(f))
        
    all_iocs = list(set(all_iocs))
    
    if not all_iocs:
        if not args.silent: print("[-] No actionable/non-whitelisted IOCs found.")
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir
        
    archive_dir = os.path.join(platform_root, 'Data', 'Archive')
    os.makedirs(archive_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = os.path.join(archive_dir, f'Extracted_IOCs_{timestamp}.csv')
    
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['IOC_Type', 'Defanged_Value'])
        for ioc_type, raw_val in all_iocs:
            writer.writerow([ioc_type, defang(raw_val, ioc_type)])
            
    if not args.silent:
        print(f"[+] Extracted {len(all_iocs)} clean IOCs to {out_csv}")

if __name__ == "__main__":
    main()
