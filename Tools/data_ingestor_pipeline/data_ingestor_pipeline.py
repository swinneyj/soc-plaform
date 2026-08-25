# TOOL_NAME: Data_Ingestor_Pipeline
# DESC: Unified pipeline for ingesting files (or folders), sanitizing PII/PHI using pseudo-NLP, scrubbing network IOCs, and formatting data.
# CATEGORY: Data Sanitization
# ARG: --target | Target File or Folder | Path to the file or folder to sanitize | False
# ARG: --mode | Processing Mode | 1 (Logs), 2 (OSINT), or 3 (Comprehensive) | False

import os
import re
import shlex
import argparse

def sanitize_logs(text):
    text = re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '[REDACTED_IPV4]', text)
    text = re.sub(r'\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b', '[REDACTED_IPV6]', text)
    text = re.sub(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b', '[REDACTED_MAC]', text)
    text = re.sub(r'(?i)ticket[-_]?#?\d+', '[REDACTED_TICKET]', text)
    return text

def sanitize_pii_phi(text):
    text = re.sub(r'\b\d{3}[-]?\d{2}[-]?\d{4}\b', '[REDACTED_SSN]', text)
    text = re.sub(r'\b(?:\d[ -]*?){13,16}\b', '[REDACTED_CC]', text)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b', '[REDACTED_EMAIL]', text)
    text = re.sub(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[REDACTED_PHONE]', text)
    name_titles = r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Sgt\.|Capt\.|Lt\.|Patient)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'
    text = re.sub(name_titles, r'\1 [REDACTED_NAME]', text)
    name_fields = r'(?i)(name[\s:]+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
    text = re.sub(name_fields, r'\1[REDACTED_NAME]', text)
    dob_context = r'(?i)(dob|date of birth|born on)[\s:]*(\d{1,4}[-/.\s][A-Za-z0-9]{2,9}[-/.\s]\d{1,4})'
    text = re.sub(dob_context, r'\1 [REDACTED_DOB]', text)
    med_context = r'(?i)(diagnosed with|treatment for|suffering from|symptoms of|history of)\s+([A-Za-z\s\-]{3,30})([.,;]|\b)'
    text = re.sub(med_context, r'\1 [REDACTED_MEDICAL_CONDITION]\3', text)
    address_pattern = r'\b\d{1,5}\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir)\b\.?'
    text = re.sub(address_pattern, '[REDACTED_ADDRESS]', text)
    return text

def process_file(filepath, mode, silent=False):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        if mode == '1':
            processed_content = sanitize_logs(content)
            out_name = f"Sanitized_Log_{os.path.basename(filepath)}"
        elif mode == '2':
            processed_content = sanitize_pii_phi(content)
            processed_content = f"Please analyze the following sanitized OSINT/document data for threats and summarize the findings:\n\n{processed_content}"
            out_name = f"Parsed_OSINT_{os.path.basename(filepath)}"
        elif mode == '3':
            processed_content = sanitize_logs(content)
            processed_content = sanitize_pii_phi(processed_content)
            processed_content = f"Please perform a comprehensive threat analysis on the following fully sanitized data:\n\n{processed_content}"
            out_name = f"Fully_Sanitized_{os.path.basename(filepath)}"
        else:
            return

        out_path = os.path.join(os.path.dirname(filepath), out_name)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(processed_content)
        
        if not silent: print(f"  [+] Saved: {out_name}")
    except Exception as e:
        if not silent: print(f"  [!] Error processing {os.path.basename(filepath)}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Data Ingestor Pipeline")
    parser.add_argument('--target', type=str, help="Target file or folder to process")
    parser.add_argument('--mode', type=str, choices=['1', '2', '3'], help="Processing mode (1, 2, or 3)")
    parser.add_argument('--silent', action='store_true', help="Run without prompts")
    args = parser.parse_args()

    if not args.silent:
        print("\n" + "="*50)
        print(" DATA INGESTOR PIPELINE (BATCH EDITION) ")
        print("="*50)
    
    mode = args.mode
    if not mode:
        print("\nSelect Processing Mode:")
        print("[1] Log Sanitization (Redact IPs, MACs, Tickets)")
        print("[2] Doc/OSINT Parsing (Redact PII & PHI, Wrap for AI Analysis)")
        print("[3] Comprehensive (Redact ALL Logs & PII/PHI, Wrap for AI Analysis)")
        mode = input("> ").strip()
    
    if mode not in ['1', '2', '3']:
        if not args.silent: print("[!] Invalid mode selected.")
        return

    raw_input = args.target
    if not raw_input:
        raw_input = input("\nDrag & drop files or a folder here:\n> ").strip()
    
    try:
        paths = shlex.split(raw_input)
    except ValueError:
        paths = [raw_input.strip('"').strip("'")]

    files_to_process = []
    for p in paths:
        p = p.strip('"').strip("'")
        if os.path.isfile(p):
            if not os.path.basename(p).startswith(('Sanitized_', 'Parsed_', 'Fully_Sanitized_')):
                files_to_process.append(p)
        elif os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in files:
                    if not f.startswith(('Sanitized_', 'Parsed_', 'Fully_Sanitized_')):
                        files_to_process.append(os.path.join(root, f))

    if not files_to_process:
        if not args.silent: print("\n[!] No valid files found to process.")
        return

    if not args.silent: print(f"\n[*] Processing {len(files_to_process)} file(s)...")
    for f in files_to_process:
        process_file(f, mode, args.silent)
    
    if not args.silent: print("\n[+] Batch processing complete!")

    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    main()
