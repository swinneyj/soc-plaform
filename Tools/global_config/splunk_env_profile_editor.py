# TOOL_NAME: Splunk_Env_Profile_Editor
# DESC: Interactive editor for splunk_env_profile.json role→SPL fragments (indexes/sourcetypes).
# CATEGORY: System Utilities

import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
if tools_dir not in sys.path:
    sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

DEFAULT_PROFILE_NAME = "splunk_env_profile.json"


def load_profile(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def save_profile(path: str, profile: dict) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2)
        print(f"{Colors.GREEN}[+] Saved profile to: {path}{Colors.ENDC}")
    except Exception as e:
        print(f"{Colors.FAIL}[!] Error saving profile: {e}{Colors.ENDC}")


def print_header(profile_path: str) -> None:
    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" SPLUNK ENV PROFILE EDITOR ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")
    print(f"\n{Colors.HEADER}Profile Path:{Colors.ENDC} {profile_path}\n")


def list_roles(profile: dict) -> None:
    if not profile:
        print(f"{Colors.WARNING}[*] No roles defined yet. Add one from the menu.{Colors.ENDC}")
        return

    print(f"{Colors.BOLD}Current roles (index/sourcetype search fragments):{Colors.ENDC}\n")
    for name, fragment in sorted(profile.items()):
        print(f"- {Colors.OKBLUE}{name}{Colors.ENDC}: {fragment}")
    print()


def prompt_role_name(prompt_text: str = "Enter role name (e.g., NOTABLE_INDEX, SSH_KEY_EVENT_SEARCH): ") -> str:
    while True:
        name = input(prompt_text).strip()
        if not name:
            print(f"{Colors.WARNING}[!] Role name cannot be empty.{Colors.ENDC}")
            continue
        return name


def add_or_update_role(profile: dict) -> None:
    print(f"{Colors.BOLD}Add or Update Role{Colors.ENDC}")
    name = prompt_role_name()

    existing = profile.get(name)
    if existing:
        print(f"{Colors.WARNING}[*] Existing fragment for {name}:{Colors.ENDC} {existing}")

    print("\nType the SPL search fragment for this role.")
    print("Example: index=nix sourcetype=linux_secure")
    print("You can paste a full fragment including index= and sourcetype= clauses.")

    fragment = input("New fragment (blank to cancel): ").strip()
    if not fragment:
        print(f"{Colors.WARNING}[!] No changes made for {name}.{Colors.ENDC}")
        return

    profile[name] = fragment
    print(f"{Colors.GREEN}[+] Role {name} set to: {fragment}{Colors.ENDC}")


def delete_role(profile: dict) -> None:
    if not profile:
        print(f"{Colors.WARNING}[*] No roles to delete.{Colors.ENDC}")
        return

    print(f"{Colors.BOLD}Delete Role{Colors.ENDC}")
    name = prompt_role_name("Enter role name to delete: ")
    if name not in profile:
        print(f"{Colors.FAIL}[!] Role {name} does not exist.{Colors.ENDC}")
        return

    confirm = input(f"Are you sure you want to delete {name}? [y/N]: ").strip().lower()
    if confirm != 'y':
        print(f"{Colors.WARNING}[*] Deletion cancelled.{Colors.ENDC}")
        return

    profile.pop(name, None)
    print(f"{Colors.GREEN}[+] Deleted role {name}.{Colors.ENDC}")


def view_role(profile: dict) -> None:
    if not profile:
        print(f"{Colors.WARNING}[*] No roles defined yet.{Colors.ENDC}")
        return

    name = prompt_role_name("Enter role name to view: ")
    value = profile.get(name)
    if value is None:
        print(f"{Colors.FAIL}[!] Role {name} does not exist.{Colors.ENDC}")
        return

    print(f"\n{Colors.OKBLUE}{name}{Colors.ENDC}: {value}\n")


def main() -> None:
    platform_root = get_platform_root()
    global_config_dir = os.path.join(platform_root, 'Tools', 'global_config')
    os.makedirs(global_config_dir, exist_ok=True)
    profile_path = os.path.join(global_config_dir, DEFAULT_PROFILE_NAME)

    profile = load_profile(profile_path)

    while True:
        print_header(profile_path)
        print("1) List roles")
        print("2) Add or update role")
        print("3) Delete role")
        print("4) View role")
        print("5) Save and exit")
        print("6) Exit without saving")

        choice = input(f"\nSelect an option [1-6]: ").strip()

        clear_screen()
        print_header(profile_path)

        if choice == '1':
            list_roles(profile)
        elif choice == '2':
            add_or_update_role(profile)
        elif choice == '3':
            delete_role(profile)
        elif choice == '4':
            view_role(profile)
        elif choice == '5':
            save_profile(profile_path, profile)
            if not os.environ.get("COMMANDER_BOOT"):
                input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")
            break
        elif choice == '6':
            print(f"{Colors.WARNING}[*] Changes were NOT saved.{Colors.ENDC}")
            if not os.environ.get("COMMANDER_BOOT"):
                input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")
            break
        else:
            print(f"{Colors.FAIL}[!] Invalid choice. Please select 1-6.{Colors.ENDC}")

        if not os.environ.get("COMMANDER_BOOT"):
            input(f"\n{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")


if __name__ == '__main__':
    main()
