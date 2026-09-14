# TOOL_NAME: Text_Sanitizer_Pipeline
# DESC: Reflows garbled text and sanitizes IPs, MACs, and common PII/PHI before opening in Notepad.
# CATEGORY: System Utilities
# ARG: --target | Target Text File | Path to the text file to sanitize directly | False
# ARG: --mode | Input Mode | 1 (Paste), 2 (File Drag & Drop) | False
# ARG: --width | Line Width | Desired wrap width (default 100) | False

import os
import sys
import argparse
import datetime
import textwrap
import re
import json

# Make core_lib available
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root


def get_console_input() -> str:
    """Read pasted text until a line containing only 'END'."""
    print("\n[*] Paste your text below.")
    print("[*] When you are finished, type 'END' on a new line and press Enter.")
    print("-" * 50)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == 'END':
            break
        lines.append(line)
    return "\n".join(lines)


def reflow_text(raw: str, width: int) -> str:
    """Reflow text with hard line breaks into paragraphs.

    Heuristics:
    - Blank lines are paragraph boundaries.
    - Bullet / numbered lines are kept as separate paragraphs.
    - Other lines in a paragraph are joined and wrapped.
    """
    lines = raw.splitlines()
    paragraphs = []
    current = []

    def flush():
        nonlocal current
        if current:
            paragraphs.append(" ".join(current))
            current = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            flush()
            paragraphs.append("")
            continue

        stripped_l = stripped.lstrip()
        # bullets / simple numbered lists
        is_bullet = stripped_l.startswith(('-', '*'))
        is_numbered = False
        if len(stripped_l) >= 3 and stripped_l[0].isdigit() and stripped_l[1] in ['.', ')']:
            is_numbered = True

        if is_bullet or is_numbered:
            flush()
            paragraphs.append(stripped)
            continue

        current.append(stripped_l)

    flush()

    wrapped = []
    for p in paragraphs:
        if not p:
            wrapped.append("")
            continue
        if len(p) <= width:
            wrapped.append(p)
        else:
            wrapped.append(textwrap.fill(p, width=width))
    return "\n".join(wrapped)


# --- Sanitization heuristics (mirrors Data_Ingestor_Pipeline) ---

def sanitize_logs_with_tokens(text: str):
    """Sanitize log-like fields and build a token→value mapping.

    Public AI will only see tokens (e.g., HOST_1, IPV4_1), while a local
    helper can later rehydrate SPL/comments by substituting the original
    values from the mapping. This keeps proprietary details local-only.
    """
    mapping = {}
    counters = {
        "IPV4": 0,
        "IPV6": 0,
        "MAC": 0,
        "HOST": 0,
    }

    def make_token(prefix: str, value: str) -> str:
        counters[prefix] += 1
        token = f"{prefix}_{counters[prefix]}"
        mapping[token] = value
        return token

    # IPv4 → IPV4_N tokens
    def repl_ipv4(m):
        return make_token("IPV4", m.group(0))

    text = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", repl_ipv4, text)

    # IPv6 (generic redaction)
    text = re.sub(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b", "[REDACTED_IPV6]", text)

    # MAC (generic redaction)
    text = re.sub(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b", "[REDACTED_MAC]", text)

    # Ticket IDs (generic "ticket123", "ticket-123", "INC123456", "SR-98765")
    text = re.sub(r"(?i)ticket[-_]?#?\d+", "[REDACTED_TICKET]", text)
    text = re.sub(r"(?i)\b(INC|SR|REQ|TASK)[-_]?[0-9]{4,}\b", "[REDACTED_TICKET]", text)

    # Simple hostnames (short alnum-plus-dash segments before a domain or by themselves)
    # e.g. ndc32-28, srv-app01, win10-lab → HOST_N tokens.
    def repl_host(m):
        return make_token("HOST", m.group(0))

    text = re.sub(r"\b([A-Za-z0-9]+[-][A-Za-z0-9-]+)\b", repl_host, text)

    return text, mapping


def sanitize_pii_phi(text: str) -> str:
    # SSN
    text = re.sub(r"\b\d{3}[-]?\d{2}[-]?\d{4}\b", "[REDACTED_SSN]", text)
    # Credit card
    text = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CC]", text)
    # Email
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", "[REDACTED_EMAIL]", text)
    # Phone
    text = re.sub(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "[REDACTED_PHONE]", text)
    # Names in title contexts
    name_titles = r"\b(Dr\.|Mr\.|Mrs\.|Ms\.|Sgt\.|Capt\.|Lt\.|Patient)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
    text = re.sub(name_titles, r"\1 [REDACTED_NAME]", text)
    # Name fields
    name_fields = r"(?i)(name[\s:]+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    text = re.sub(name_fields, r"\1[REDACTED_NAME]", text)
    # DOB
    dob_context = r"(?i)(dob|date of birth|born on)[\s:]*(\d{1,4}[-/.\s][A-Za-z0-9]{2,9}[-/.\s]\d{1,4})"
    text = re.sub(dob_context, r"\1 [REDACTED_DOB]", text)
    # Medical condition context
    med_context = r"(?i)(diagnosed with|treatment for|suffering from|symptoms of|history of)\s+([A-Za-z\s\-]{3,30})([.,;]|\b)"
    text = re.sub(med_context, r"\1 [REDACTED_MEDICAL_CONDITION]\3", text)
    # Simple street addresses
    address_pattern = r"\b\d{1,5}\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir)\b\.?"
    text = re.sub(address_pattern, "[REDACTED_ADDRESS]", text)
    return text


def sanitize_text(raw: str, width: int):
    """Reflow and sanitize text for safe AI use, plus build a token map.

    Returns (sanitized_text, mapping_dict).
    """
    reflowed = reflow_text(raw, width)
    cleaned, mapping = sanitize_logs_with_tokens(reflowed)
    cleaned = sanitize_pii_phi(cleaned)
    return cleaned, mapping


def main():
    parser = argparse.ArgumentParser(description="Text Sanitizer Pipeline", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--target', type=str, help="Path to the text file to sanitize directly")
    parser.add_argument('--mode', type=str, choices=['1', '2'], help="Input mode: 1 (Paste), 2 (File)")
    parser.add_argument('--width', type=int, default=100, help="Wrap width (default 100)")
    parser.add_argument('--silent', action='store_true', help="Suppress UI messages")
    args = parser.parse_args()

    platform_root = get_platform_root()
    active_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
    os.makedirs(active_dir, exist_ok=True)

    if not args.silent:
        clear_screen()
        print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
        print(" TEXT SANITIZER PIPELINE ".center(80))
        print("=" * 80 + f"{Colors.ENDC}")

    # Non-interactive direct-file mode for playbooks and Smart Ingestion
    if args.target:
        target_path = args.target.strip('"').strip("'")
        if not os.path.exists(target_path):
            if not args.silent:
                print(f"{Colors.FAIL}[!] File not found: {target_path}{Colors.ENDC}")
            return

        try:
            with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_text = f.read()
        except Exception as e:
            if not args.silent:
                print(f"{Colors.FAIL}[!] Error reading file: {e}{Colors.ENDC}")
            return

        sanitized, mapping = sanitize_text(raw_text, args.width)

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_name = f"Sanitized_Text_{timestamp}.txt"
        out_path = os.path.join(active_dir, out_name)

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(sanitized)

        # Maintain a stable alias for downstream automation
        latest_alias = os.path.join(active_dir, 'Sanitized_Text_latest.txt')
        try:
            import shutil
            shutil.copy2(out_path, latest_alias)
        except Exception:
            pass

        # Write a local-only mapping file so a helper can later rehydrate
        # AI output by substituting tokens (HOST_N, IPV4_N) back to their
        # original values.
        map_path = os.path.join(active_dir, f"Sanitized_Text_{timestamp}.map.json")
        latest_map = os.path.join(active_dir, 'Sanitized_Text_latest.map.json')
        try:
            with open(map_path, 'w', encoding='utf-8') as mf:
                json.dump(mapping, mf, indent=2)
            try:
                import shutil
                shutil.copy2(map_path, latest_map)
            except Exception:
                pass
        except Exception:
            if not args.silent:
                print(f"{Colors.WARNING}[!] Could not write mapping file: {map_path}{Colors.ENDC}")

        if not args.silent:
            print(f"\n{Colors.GREEN}[+] Sanitized, reflowed text saved to:{Colors.ENDC} {out_path}")
            print(f"{Colors.CYAN}[i] Opening in Notepad (default text editor) for review...{Colors.ENDC}")

        try:
            if os.name == 'nt':
                os.startfile(out_path)
        except Exception as e:
            if not args.silent:
                print(f"{Colors.WARNING}[!] Could not auto-open file: {e}{Colors.ENDC}")

        if not args.silent and not os.environ.get("COMMANDER_BOOT"):
            input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")
        return

    # Interactive modes (used when no --target is provided)

    mode = args.mode

    if not mode:
        if not args.silent:
            print("\nHow would you like to provide the text?")
            print("[1] Paste text (END to finish)")
            print("[2] Drag & Drop or type a file path")
        # User can just press Enter to accept the default (Paste)
        choice = input("\n> ").strip()
        if choice in ('1', '2'):
            mode = choice
        else:
            # Default to Paste mode on blank/invalid input to keep the workflow smooth
            if not args.silent:
                print(f"{Colors.WARNING}[*] No valid mode selected, defaulting to Paste (1).{Colors.ENDC}")
            mode = '1'

    raw_text = ""
    source_name = ""

    if mode == '1':
        raw_text = get_console_input()
        if not raw_text.strip():
            if not args.silent:
                print(f"{Colors.FAIL}[!] No text provided.{Colors.ENDC}")
            return
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        source_name = f"Pasted_Text_{timestamp}.txt"
        raw_path = os.path.join(active_dir, source_name)
        with open(raw_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
    elif mode == '2':
        # Interactive file-path mode: keep prompting until a valid path is provided
        # so that the workflow can "wait" for drag & drop instead of failing on a blank line.
        while True:
            if not args.silent:
                print("\nDrag & drop the text file path below, or type it (or 'q' to cancel):")
            raw_input_path = input("> ").strip()

            # Allow explicit cancel
            if not raw_input_path:
                if not args.silent:
                    print(f"{Colors.WARNING}[*] No path entered. Drag & drop a file or type 'q' to cancel.{Colors.ENDC}")
                continue
            if raw_input_path.lower() in ('q', 'quit', 'exit', '0'):
                if not args.silent:
                    print(f"{Colors.WARNING}[*] Text sanitizer cancelled by user.{Colors.ENDC}")
                return

            raw_input_path = raw_input_path.strip('"').strip("'")
            if not os.path.exists(raw_input_path):
                if not args.silent:
                    print(f"{Colors.FAIL}[!] File not found: {raw_input_path}{Colors.ENDC}")
                # Re-prompt instead of exiting immediately
                continue

            with open(raw_input_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_text = f.read()
            source_name = os.path.basename(raw_input_path)
            break
    else:
        if not args.silent:
            print(f"{Colors.FAIL}[!] Invalid mode selection.{Colors.ENDC}")
        return

    sanitized, mapping = sanitize_text(raw_text, args.width)

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_name = f"Sanitized_Text_{timestamp}.txt"
    out_path = os.path.join(active_dir, out_name)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(sanitized)

    # Maintain a stable alias for downstream automation (e.g., playbooks
    # that need "the most recent sanitized text" without guessing the
    # timestamped file name).
    latest_alias = os.path.join(active_dir, 'Sanitized_Text_latest.txt')
    try:
        import shutil
        shutil.copy2(out_path, latest_alias)
    except Exception:
        pass

    # Write a local-only mapping file (interactive mode)
    map_path = os.path.join(active_dir, f"Sanitized_Text_{timestamp}.map.json")
    latest_map = os.path.join(active_dir, 'Sanitized_Text_latest.map.json')
    try:
        with open(map_path, 'w', encoding='utf-8') as mf:
            json.dump(mapping, mf, indent=2)
        try:
            import shutil
            shutil.copy2(map_path, latest_map)
        except Exception:
            pass
    except Exception:
        if not args.silent:
            print(f"{Colors.WARNING}[!] Could not write mapping file: {map_path}{Colors.ENDC}")

    if not args.silent:
        print(f"\n{Colors.GREEN}[+] Sanitized, reflowed text saved to:{Colors.ENDC} {out_path}")
        print(f"{Colors.CYAN}[i] Opening in Notepad (default text editor) for review...{Colors.ENDC}")

    try:
        if os.name == 'nt':
            os.startfile(out_path)
    except Exception as e:
        if not args.silent:
            print(f"{Colors.WARNING}[!] Could not auto-open file: {e}{Colors.ENDC}")

    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")


if __name__ == "__main__":
    main()
