# TOOL_NAME: Splunk_Env_Profile_Builder
# DESC: Scans a local folder of Splunk index/sourcetype metadata and refreshes splunk_env_profile.json with role→SPL fragments.
# CATEGORY: System Utilities
# ARG: --source_dir | Source Directory | Folder containing ROLE-marked index/sourcetype reference files | False

import os
import sys
import argparse
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
if tools_dir not in sys.path:
    sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

DEFAULT_PROFILE_NAME = "splunk_env_profile.json"


def load_existing_profile(profile_path: str) -> dict:
    if not os.path.exists(profile_path):
        return {}
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def scan_source_dir(source_dir: str) -> dict:
    """Scan a folder for simple text/JSON files that describe indexes/sourcetypes.

    This is intentionally conservative: it looks for lines or keys like
    'index=', 'sourcetype=' and allows a human to pre-populate the
    directory with reference files rather than trying to discover the
    Splunk config directly.
    """
    roles = {}

    if not os.path.exists(source_dir):
        return roles

    for root, _, files in os.walk(source_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue

            # Simple heuristics: look for role headers like [ROLE:<NAME>]
            # followed by one or more lines of SPL fragments.
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('[ROLE:') and line.endswith(']'):
                    role_name = line[len('[ROLE:'):-1].strip()
                    roles.setdefault(role_name, [])
                elif roles:
                    # Attach subsequent non-empty lines to the last role
                    # until a new [ROLE:...] header appears.
                    last_role = list(roles.keys())[-1]
                    roles[last_role].append(line)

    # Collapse roles into single SPL fragments (OR-joined where needed).
    collapsed = {}
    for role, fragments in roles.items():
        fragments = [f for f in fragments if f]
        if not fragments:
            continue
        if len(fragments) == 1:
            collapsed[role] = fragments[0]
        else:
            # Join multiple fragments with OR for convenience.
            collapsed[role] = "(" + " OR ".join(fragments) + ")"

    return collapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Splunk Env Profile Builder")
    parser.add_argument('--source_dir', type=str, required=False,
                        help="Folder containing index/sourcetype reference files (ROLE-marked)")
    args = parser.parse_args()

    platform_root = get_platform_root()
    global_config_dir = os.path.join(platform_root, 'Tools', 'global_config')
    os.makedirs(global_config_dir, exist_ok=True)
    profile_path = os.path.join(global_config_dir, DEFAULT_PROFILE_NAME)

    if not args.source_dir:
        source_dir = os.path.join(global_config_dir, 'splunk_env_sources')
    else:
        source_dir = args.source_dir.strip('"').strip("'")
        if not os.path.isabs(source_dir):
            source_dir = os.path.join(platform_root, source_dir)

    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" SPLUNK ENV PROFILE BUILDER ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")
    print(f"\n{Colors.HEADER}Profile Path:{Colors.ENDC} {profile_path}")
    print(f"{Colors.HEADER}Source Dir:{Colors.ENDC} {source_dir}\n")

    existing = load_existing_profile(profile_path)
    discovered = scan_source_dir(source_dir)

    # Merge discovered roles into existing profile (discovered wins).
    merged = dict(existing)
    merged.update(discovered)

    try:
        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(merged, f, indent=2)
        print(f"{Colors.GREEN}[+] Updated Splunk env profile with {len(discovered)} role(s).{Colors.ENDC}")
        print(f"[*] Saved to: {profile_path}")
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error writing profile: {e}{Colors.ENDC}")

    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")


if __name__ == '__main__':
    main()
