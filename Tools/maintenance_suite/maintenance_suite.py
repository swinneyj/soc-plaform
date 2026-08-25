# TOOL_NAME: Maintenance_Suite
# DESC: Interactive hub for cleaning the workspace, generating AI context exports, or backing up the platform.
# CATEGORY: System Utilities

import os
import sys
import subprocess
import shutil
import datetime

# Dynamically add the Tools directory to the path so we can load core_lib
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

def run_tool(script_name, base_dir, touchless=False):
    """Searches for the requested tool in the Tools directory and runs it."""
    script_path = None
    tools_folder = os.path.join(base_dir, 'Tools')
    
    for root, dirs, files in os.walk(tools_folder):
        if script_name in files:
            script_path = os.path.join(root, script_name)
            break
            
    if script_path:
        print(f"\n{Colors.WARNING}[*] Launching module: {script_name}...{Colors.ENDC}")
        if touchless:
            subprocess.run([sys.executable, script_path], input=b"\n\n\n")
        else:
            subprocess.run([sys.executable, script_path])
    else:
        print(f"\n{Colors.FAIL}[!] Error: Could not locate {script_name} within the Tools directory.{Colors.ENDC}")

def route_files(base_dir):
    """Moves generated maintenance files to their proper, decoupled directories."""
    maintenance_dir = os.path.join(base_dir, 'Data', 'Maintenance')
    exports_dir = os.path.join(base_dir, 'Data', 'Exports')
    
    os.makedirs(maintenance_dir, exist_ok=True)
    os.makedirs(exports_dir, exist_ok=True)

    print(f"\n{Colors.CYAN}[*] Routing generated files to designated folders...{Colors.ENDC}")
    
    # Route Context Exports
    for file in os.listdir(base_dir):
        if file.startswith("AI_Context_Export") and file.endswith(".txt"):
            src = os.path.join(base_dir, file)
            dst = os.path.join(exports_dir, file)
            shutil.move(src, dst)
            print(f"  {Colors.GREEN}[+]{Colors.ENDC} Routed {file} -> /Data/Exports/")
            
    # Route Maps and Zips
    for file in os.listdir(base_dir):
        if (file.endswith(".md") or file.endswith("Map.txt") or file.endswith(".zip")) and not file.startswith("README"):
            src = os.path.join(base_dir, file)
            dst = os.path.join(maintenance_dir, file)
            shutil.move(src, dst)
            print(f"  {Colors.GREEN}[+]{Colors.ENDC} Routed {file} -> /Data/Maintenance/")

def main():
    base_dir = get_platform_root()
    
    while True:
        try:
            clear_screen()
            print(f"\n{Colors.CYAN}{Colors.BOLD}" + "="*80)
            print(" SOC PLATFORM MAINTENANCE SUITE ".center(80))
            print("="*80 + f"{Colors.ENDC}")
            
            print(f"\n{Colors.HEADER}--- SELECT A MAINTENANCE MODULE ---{Colors.ENDC}")
            print(f" {Colors.GREEN}[1]{Colors.ENDC} Clean Workspace       | Archives old logs and removes empty directories")
            print(f" {Colors.GREEN}[2]{Colors.ENDC} Map Platform          | Generates a blueprint map of the SOC directory")
            print(f" {Colors.GREEN}[3]{Colors.ENDC} Export AI Context     | Extracts ONLY source code (.py/.json/.md) for AI sharing (saved with Blueprint in Data/Exports)")
            print(f" {Colors.GREEN}[4]{Colors.ENDC} Prep AI Context (Combo)| Runs Blueprint + AI Context Export into Data/Exports")
            print(f" {Colors.GREEN}[5]{Colors.ENDC} Full Platform Backup  | Compresses the entire platform into a deployable ZIP")
            print(f" {Colors.GREEN}[6]{Colors.ENDC} Run Full Suite        | Runs Cleaning -> Mapping -> Full Backup sequentially")
            print(f" {Colors.GREEN}[7]{Colors.ENDC} Platform Health Check | Validates registry, tools, and playbooks for basic integrity")
            print(f" {Colors.GREEN}[0]{Colors.ENDC} Exit                  | Return to Commander")
            
            choice = input(f"\n{Colors.BLUE}maintenance>{Colors.ENDC} ").strip()
            
            if choice == '1':
                run_tool('workspace_cleaner.py', base_dir)
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")
                
            elif choice == '2':
                run_tool('platform_blueprint.py', base_dir)
                route_files(base_dir)
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")
                
            elif choice == '3':
                run_tool('ai_context_exporter.py', base_dir)
                route_files(base_dir)
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")
                
            elif choice == '4':
                print(f"\n{Colors.WARNING}[*] Running combined Blueprint + AI Context Export...{Colors.ENDC}")
                run_tool('platform_blueprint.py', base_dir, touchless=True)
                run_tool('ai_context_exporter.py', base_dir, touchless=True)
                route_files(base_dir)
                print(f"\n{Colors.GREEN}[+] Combined AI context artifacts saved to /Data/Exports/.{Colors.ENDC}")
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")

            elif choice == '5':
                run_tool('tool_packager.py', base_dir)
                route_files(base_dir)
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")
                
            elif choice == '6':
                print(f"\n{Colors.WARNING}[*] Initiating Full Maintenance Sequence...{Colors.ENDC}")
                run_tool('workspace_cleaner.py', base_dir, touchless=True)
                run_tool('platform_blueprint.py', base_dir, touchless=True)
                run_tool('tool_packager.py', base_dir, touchless=True)
                route_files(base_dir)
                print(f"\n{Colors.GREEN}[+] Full Maintenance Sequence Complete.{Colors.ENDC}")
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")

            elif choice == '7':
                run_tool('platform_health_check.py', base_dir)
                input(f"\n{Colors.BLUE}Press Enter to return to Maintenance Suite...{Colors.ENDC}")
                
            elif choice == '0' or choice.lower() in ['exit', 'quit']:
                break
            else:
                print(f"{Colors.FAIL}[!] Invalid selection.{Colors.ENDC}")
                input(f"\n{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.WARNING}[*] Operation aborted by user. Returning to Maintenance Menu...{Colors.ENDC}")
            input(f"{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")
            continue

if __name__ == "__main__":
    main()
