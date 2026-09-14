# TOOL_NAME: pki_cert_decoder
# DESC: Decodes and analyzes X.509 certificates (.pem, .cer, .der, or raw base64) locally without external network calls.
# CATEGORY: Intel Collection
# ARG: --cert | Certificate File | Path to the certificate file to analyze | True

import os
import sys
import argparse
import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, get_platform_root

try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.x509.oid import NameOID
    import binascii
except ImportError:
    print(f"{Colors.FAIL}[!] Critical Error: 'cryptography' library is not installed.{Colors.ENDC}")
    print(f"    Please run: pip install cryptography")
    sys.exit(1)

def get_attribute(name_obj, oid):
    """Safely extracts an attribute from an x509 Name object."""
    attributes = name_obj.get_attributes_for_oid(oid)
    if attributes:
        return attributes[0].value
    return "Not Specified"

def format_fingerprint(fingerprint_bytes):
    """Formats raw bytes into a standard hex string (e.g., AA:BB:CC)."""
    hex_str = binascii.hexlify(fingerprint_bytes).decode('ascii').upper()
    return ":".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))

def analyze_certificate(cert_path, silent=False):
    if not silent: print(f"\n{Colors.CYAN}[*] Loading certificate from: {os.path.basename(cert_path)}{Colors.ENDC}")
    
    try:
        with open(cert_path, 'rb') as f:
            cert_data = f.read()
    except Exception as e:
        if not silent: print(f"{Colors.FAIL}[!] Error reading file: {e}{Colors.ENDC}")
        return

    # Attempt to load as PEM (Base64), fallback to DER (Binary)
    cert = None
    try:
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
    except ValueError:
        try:
            cert = x509.load_der_x509_certificate(cert_data, default_backend())
        except Exception as e:
            if not silent: print(f"{Colors.FAIL}[!] Failed to parse certificate. Ensure it is a valid PEM or DER format.{Colors.ENDC}")
            return

    # Extract Data
    subject = cert.subject
    issuer = cert.issuer
    
    sub_cn = get_attribute(subject, NameOID.COMMON_NAME)
    sub_org = get_attribute(subject, NameOID.ORGANIZATION_NAME)
    
    iss_cn = get_attribute(issuer, NameOID.COMMON_NAME)
    iss_org = get_attribute(issuer, NameOID.ORGANIZATION_NAME)

    not_valid_before = cert.not_valid_before_utc
    not_valid_after = cert.not_valid_after_utc
    
    # Check Validity
    now = datetime.datetime.now(datetime.timezone.utc)
    status_color = Colors.GREEN
    status_text = "VALID"
    
    if now < not_valid_before:
        status_color = Colors.WARNING
        status_text = "NOT YET VALID"
    elif now > not_valid_after:
        status_color = Colors.FAIL
        status_text = "EXPIRED"

    # Get Fingerprints
    from cryptography.hazmat.primitives import hashes
    sha256_fp = format_fingerprint(cert.fingerprint(hashes.SHA256()))
    sha1_fp = format_fingerprint(cert.fingerprint(hashes.SHA1()))

    # Output formatting
    if not silent:
        print(f"\n{Colors.CYAN}{Colors.BOLD}" + "="*80)
        print(" PKI CERTIFICATE ANALYSIS ".center(80))
        print("="*80 + f"{Colors.ENDC}")
        
        print(f"\n{Colors.BOLD}STATUS:{Colors.ENDC} [{status_color}{status_text}{Colors.ENDC}]")
        
        print(f"\n{Colors.HEADER}--- SUBJECT (Issued To) ---{Colors.ENDC}")
        print(f"{Colors.BOLD}Common Name (CN):{Colors.ENDC}   {sub_cn}")
        print(f"{Colors.BOLD}Organization (O):{Colors.ENDC}   {sub_org}")
        
        print(f"\n{Colors.HEADER}--- ISSUER (Issued By) ---{Colors.ENDC}")
        print(f"{Colors.BOLD}Common Name (CN):{Colors.ENDC}   {iss_cn}")
        print(f"{Colors.BOLD}Organization (O):{Colors.ENDC}   {iss_org}")
        
        print(f"\n{Colors.HEADER}--- VALIDITY PERIOD ---{Colors.ENDC}")
        print(f"{Colors.BOLD}Issued On:{Colors.ENDC}          {not_valid_before.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{Colors.BOLD}Expires On:{Colors.ENDC}         {not_valid_after.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        print(f"\n{Colors.HEADER}--- FINGERPRINTS ---{Colors.ENDC}")
        print(f"{Colors.BOLD}SHA-256:{Colors.ENDC}            {sha256_fp}")
        print(f"{Colors.BOLD}SHA-1:{Colors.ENDC}              {sha1_fp}")
        
        # Self-Signed Check
        if subject == issuer:
            print(f"\n{Colors.WARNING}[!] WARNING: This certificate is SELF-SIGNED.{Colors.ENDC}")

        print(f"{Colors.CYAN}" + "="*80 + f"{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(description="PKI Certificate Decoder")
    parser.add_argument('--cert', type=str, required=True, help="Path to the certificate file")
    parser.add_argument('--silent', action='store_true', help="Suppress terminal output")
    args = parser.parse_args()

    cert_path = args.cert.strip('"').strip("'")
    if not os.path.exists(cert_path):
        if not args.silent: print(f"{Colors.FAIL}[-] File not found: {cert_path}{Colors.ENDC}")
        return

    analyze_certificate(cert_path, args.silent)
    
    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
