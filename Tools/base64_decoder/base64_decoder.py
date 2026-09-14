# TOOL_NAME: base64_decoder
# DESC: Extracts, cleans, and decodes Base64 payloads from raw text or files. Ideal for de-obfuscating PowerShell payloads or malicious scripts.
# CATEGORY: Intel Collection
# ARG: --target | Target File or Text | File containing Base64 or a raw Base64 string | True

import os
import sys
import base64
import re
import argparse

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, get_platform_root

def clean_base64(raw_string):
    """Removes whitespace, newlines, and fixes padding."""
    clean = re.sub(r'\s+', '', raw_string)
    # Fix padding
    missing_padding = len(clean) % 4
    if missing_padding:
        clean += '=' * (4 - missing_padding)
    return clean

def decode_payload(target, silent=False):
    is_file = os.path.isfile(target)
    content = ""
    
    if is_file:
        try:
            with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            if not silent: print(f"{Colors.FAIL}[!] Error reading file: {e}{Colors.ENDC}")
            return
    else:
        # If it's not a file path, assume the user pasted a raw base64 string directly
        content = target

    # Try to find base64-like strings (very permissive regex to catch embedded payloads)
    b64_matches = re.findall(r'(?:[A-Za-z0-9+/]{4}){2,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?', content)
    
    if not b64_matches:
        if not silent: print(f"{Colors.FAIL}[-] No Base64 patterns detected in the target.{Colors.ENDC}")
        return

    if not silent: print(f"{Colors.CYAN}[*] Found {len(b64_matches)} potential Base64 strings...{Colors.ENDC}\n")

    for i, match in enumerate(b64_matches):
        cleaned = clean_base64(match)
        try:
            decoded_bytes = base64.b64decode(cleaned)
            
            # Try to decode as utf-8, fallback to utf-16-le (common for PowerShell), then raw repr
            try:
                decoded_str = decoded_bytes.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    decoded_str = decoded_bytes.decode('utf-16-le') 
                except:
                    decoded_str = repr(decoded_bytes)
            
            if not silent:
                print(f"{Colors.HEADER}--- Match {i+1} ---{Colors.ENDC}")
                print(f"{Colors.BLUE}Raw Length:{Colors.ENDC} {len(match)}")
                print(f"{Colors.GREEN}Decoded:{Colors.ENDC}\n{decoded_str}\n")
                
        except Exception as e:
            if not silent: print(f"{Colors.FAIL}[!] Failed to decode Match {i+1}: {e}{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(description="Base64 Payload Decoder")
    parser.add_argument('--target', type=str, required=True, help="File or raw string to decode")
    # Add the missing silent flag to argparse
    parser.add_argument('--silent', action='store_true', help="Suppress terminal output for playbook automation")
    args = parser.parse_args()

    # Strip quotes if dragged from Windows Explorer
    target = args.target.strip('"').strip("'")
    decode_payload(target, args.silent)
    
    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
