# TOOL_NAME: Automated_Reporter
# DESC: Compiles text summaries and sanitized intelligence into a standardized, shareable HTML SOC briefing document.
# CATEGORY: Reporting
# ARG: --mode | Input Mode | 1 for File, 2 for Paste | False

import os
import datetime
import html
import argparse

def generate_report_from_text(content, source_name, output_dir, silent=False):
    safe_content = html.escape(content)
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    date_str = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    report_filename = f"SOC_Briefing_{date_str}.html"
    report_path = os.path.join(output_dir, report_filename)

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>SOC Intelligence Briefing</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background-color: #f4f4f9; color: #333; }}
        .container {{ background-color: #ffffff; padding: 40px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); max-width: 900px; margin: auto; }}
        .header {{ border-bottom: 3px solid #005A9C; padding-bottom: 15px; margin-bottom: 25px; }}
        .classification {{ color: #2E7D32; font-weight: bold; text-align: center; font-size: 1.2em; margin-bottom: 15px; letter-spacing: 2px; }}
        h1 {{ color: #005A9C; margin-top: 0; font-size: 2.2em; }}
        .meta-data {{ font-size: 0.95em; color: #555; background-color: #f0f0f5; padding: 10px; border-radius: 4px; border-left: 4px solid #005A9C; }}
        pre {{ background-color: #f9f9fc; padding: 20px; border: 1px solid #ddd; border-radius: 5px; font-family: 'Courier New', Courier, monospace; font-size: 1em; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="classification">UNCLASSIFIED // CUI</div>
    <div class="container">
        <div class="header">
            <h1>Executive Intelligence Briefing</h1>
            <div class="meta-data">
                <strong>Date Generated:</strong> {timestamp}<br>
                <strong>Source Data:</strong> {source_name}<br>
                <strong>Classification:</strong> UNCLASSIFIED // CUI
            </div>
        </div>
        <div class="content">
            <pre>{safe_content}</pre>
        </div>
    </div>
    <div class="classification" style="margin-top: 25px;">UNCLASSIFIED // CUI</div>
</body>
</html>
"""

    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        if not silent:
            print(f"\n  [+] Executive Briefing successfully generated!")
            print(f"  [+] Location: {report_path}")
            print("  [i] Tip: Open this file in your browser and select 'Print > Save as PDF' for a shareable document.")
    except Exception as e:
        if not silent: print(f"  [!] Error generating report: {e}")

def get_console_input():
    print("\n[*] Paste your text below.")
    print("[*] When you are finished pasting, type 'END' on a new blank line and press Enter.")
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

def main():
    parser = argparse.ArgumentParser(description="Automated Reporting Module")
    parser.add_argument('--mode', type=str, choices=['1', '2'], help="Input mode: 1 (File) or 2 (Paste)")
    parser.add_argument('--file', type=str, help="Optional path to a pre-generated text report (bypasses interactive file prompt when set)")
    parser.add_argument('--silent', action='store_true', help="Run silently")
    args = parser.parse_args()

    if not args.silent:
        print("\n" + "="*50)
        print(" AUTOMATED REPORTING MODULE ")
        print("="*50)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir
        
    output_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
    os.makedirs(output_dir, exist_ok=True)

    choice = args.mode

    # Fast path: if --file is provided, use it directly as the source
    if args.file:
        filepath = args.file.strip('"').strip("'")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                generate_report_from_text(content, os.path.basename(filepath), output_dir, args.silent)
            except Exception as e:
                if not args.silent: print(f"\n[!] Error reading file: {e}")
        else:
            if not args.silent: print(f"\n[!] File not found: {filepath}")

    else:
        if not choice:
            print("\nHow would you like to provide the report data?")
            print("[1] Drag & Drop a text file path")
            print("[2] Paste raw text directly into the console")
            choice = input("> ").strip()

        if choice == '1':
            filepath = input("\nEnter the full path to the file:\n> ").strip()
            filepath = filepath.strip('"').strip("'")
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                generate_report_from_text(content, os.path.basename(filepath), output_dir, args.silent)
            else:
                if not args.silent: print(f"\n[!] File not found: {filepath}")

        elif choice == '2':
            content = get_console_input()
            if content.strip():
                if not args.silent: print("\n[*] Compiling HTML Executive Briefing...")
                generate_report_from_text(content, "Direct Console Input", output_dir, args.silent)
            else:
                if not args.silent: print("\n[!] No text provided.")

        else:
            if not args.silent: print("\n[!] Invalid selection.")

    if not args.silent and not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    main()
