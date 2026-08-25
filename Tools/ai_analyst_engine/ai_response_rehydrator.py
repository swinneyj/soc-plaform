# TOOL_NAME: AI_Response_Rehydrator
# DESC: Replaces placeholder tokens (e.g., HOST_1, IPV4_1) in an AI response with real values from the sanitizer mapping file.
# CATEGORY: Reporting
# ARG: --response | AI Response File | Path to the AI model output to rehydrate | True
# ARG: --map | Mapping File | Optional path to the .map.json produced by the sanitizer | False

import os
import sys
import argparse
import datetime
import json

# Ensure Tools/core_lib are available
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
if tools_dir not in sys.path:
    sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root


def load_mapping(map_path: str) -> dict:
    try:
        with open(map_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error reading mapping file {map_path}: {e}{Colors.ENDC}")
        return {}


def rehydrate_response(response_path: str, mapping: dict, output_dir: str) -> str:
    try:
        with open(response_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error reading AI response file {response_path}: {e}{Colors.ENDC}")
        return ""

    # Apply token substitutions. We keep it simple: exact string replace
    # for each token key. Mapping stays local-only and is never sent to
    # the public AI.
    for token, value in mapping.items():
        content = content.replace(token, value)

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = os.path.basename(response_path)
    out_name = f"Rehydrated_{base_name}_{timestamp}.txt"
    out_path = os.path.join(output_dir, out_name)

    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error writing rehydrated file {out_path}: {e}{Colors.ENDC}")
        return ""

    print(f"\n{Colors.GREEN}[+] Rehydrated AI response saved to:{Colors.ENDC} {out_path}")
    try:
        if os.name == 'nt':
            os.startfile(out_path)
    except Exception:
        pass

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Response Rehydrator")
    parser.add_argument('--response', type=str, required=True,
                        help="Path to the AI response file (generic SPL/comments using tokens)")
    parser.add_argument('--map', type=str, required=False,
                        help="Optional path to the sanitizer .map.json file; defaults to Sanitized_Text_latest.map.json")
    args = parser.parse_args()

    platform_root = get_platform_root()
    active_dir = os.path.join(platform_root, 'Data', 'Active_Workspace')
    os.makedirs(active_dir, exist_ok=True)

    response_path = args.response.strip('"').strip("'")
    if not os.path.isabs(response_path):
        response_path = os.path.join(platform_root, response_path)

    if not os.path.exists(response_path):
        print(f"{Colors.FAIL}[!] AI response file not found: {response_path}{Colors.ENDC}")
        return

    if args.map:
        map_path = args.map.strip('"').strip("'")
        if not os.path.isabs(map_path):
            map_path = os.path.join(platform_root, map_path)
    else:
        map_path = os.path.join(active_dir, 'Sanitized_Text_latest.map.json')

    if not os.path.exists(map_path):
        print(f"{Colors.FAIL}[!] Mapping file not found: {map_path}{Colors.ENDC}")
        print(f"{Colors.WARNING}[i] Ensure you ran the Text_Sanitizer_Pipeline on the evidence first, so it created a .map.json.{Colors.ENDC}")
        return

    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" AI RESPONSE REHYDRATOR ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")

    mapping = load_mapping(map_path)
    if not mapping:
        return

    rehydrate_response(response_path, mapping, active_dir)

    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")


if __name__ == '__main__':
    main()
