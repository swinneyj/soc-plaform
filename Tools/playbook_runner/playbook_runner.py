# TOOL_NAME: Playbook_Runner
# DESC: Executes pre-defined JSON sequences of SOC tools (Playbooks) for rapid automated workflows.
# CATEGORY: Core Orchestration
# ARG: --playbook | JSON Playbook | Name of the JSON playbook in the Playbooks folder (e.g., ai_analysis_pipeline.json) | False
# ARG: --target | Dynamic Target | File or folder path injected into playbook steps (used for {{DYNAMIC_TARGET}}) | False

import os
import sys
import json
import subprocess
import shlex
import argparse

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from core_lib.utils import Colors, clear_screen, get_platform_root

def load_registry(platform_root):
    reg_path = os.path.join(platform_root, 'Commander_Registry.json')
    if not os.path.exists(reg_path): return []
    with open(reg_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_tool_path(tool_name, registry):
    for tool in registry:
        if tool['name'].lower() == tool_name.lower() or tool.get('file_name', '').lower().replace('.py', '') == tool_name.lower():
            return tool['path']
    return None

def main():
    parser = argparse.ArgumentParser(description="SOC Playbook Runner")
    parser.add_argument('--playbook', type=str, help="Path to the JSON playbook", default=None)
    parser.add_argument('--target', type=str, help="Dynamic target file/folder for playbooks that use {{DYNAMIC_TARGET}}", default=None)
    args = parser.parse_args()

    platform_root = get_platform_root()
    playbooks_dir = os.path.join(platform_root, 'Playbooks')
    registry = load_registry(platform_root)
    os.makedirs(playbooks_dir, exist_ok=True)
    
    os.system('')
    clear_screen()
    selected_pb = None

    try:
        if args.playbook:
            playbook_path = args.playbook.strip('"').strip("'")
            try:
                with open(playbook_path, 'r', encoding='utf-8') as f:
                    selected_pb = json.load(f)

                if not selected_pb.get('enabled', True):
                    print(f"\n{Colors.WARNING}[*] This playbook is currently DISABLED and cannot be run.{Colors.ENDC}")
                    if not os.environ.get("COMMANDER_BOOT"): input("\nPress Enter to return...")
                    return
            except Exception as e:
                print(f"{Colors.FAIL}[!] Error loading playbook from {playbook_path}: {e}{Colors.ENDC}")
                input("\nPress Enter to return...")
                return
        else:
            print(f"{Colors.CYAN}{Colors.BOLD}" + "="*80)
            print(" SOC PLAYBOOK RUNNER (DYNAMIC)".center(80))
            print("="*80 + f"{Colors.ENDC}")
            
            available_playbooks = []
            for file in os.listdir(playbooks_dir):
                if file.endswith('.json'):
                    filepath = os.path.join(playbooks_dir, file)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            pb_data = json.load(f)
                            available_playbooks.append({
                                'id': len(available_playbooks) + 1, 'file': file,
                                'name': pb_data.get('name', file), 'description': pb_data.get('description', 'No description'),
                                'path': filepath, 'steps': pb_data.get('steps', []),
                                'enabled': pb_data.get('enabled', True)
                            })
                    except Exception as e:
                        print(f"{Colors.FAIL}[!] Error loading {file}: {e}{Colors.ENDC}")
                        
            if not available_playbooks:
                print(f"\n{Colors.FAIL}[!] No JSON playbooks found.{Colors.ENDC}")
                input("\nPress Enter to return...")
                return
                
            print(f"\n{Colors.HEADER}--- AVAILABLE PLAYBOOKS ---{Colors.ENDC}")
            for pb in available_playbooks:
                status_label = "" if pb.get('enabled', True) else f"{Colors.WARNING} (DISABLED){Colors.ENDC}"
                print(f" {Colors.GREEN}[{pb['id']}]{Colors.ENDC} {pb['name']:<25} | {pb['description']}{status_label}")
                
            choice = input(f"\n{Colors.CYAN}Enter Playbook ID to run:{Colors.ENDC} ").strip()
            
            selected_match = next((p for p in available_playbooks if str(p['id']) == choice), None)
            if not selected_match:
                print(f"{Colors.FAIL}[!] Invalid selection.{Colors.ENDC}")
                input("\nPress Enter to return...")
                return

            if not selected_match.get('enabled', True):
                print(f"\n{Colors.WARNING}[*] This playbook is currently DISABLED.{Colors.ENDC}")
                input("\nPress Enter to return...")
                return

            selected_pb = selected_match

        print(f"\n{Colors.CYAN}{Colors.BOLD}" + "="*80)
        print(f" INITIATING PLAYBOOK: {selected_pb.get('name', 'UNKNOWN').upper()} ".center(80))
        print("="*80 + f"{Colors.ENDC}")
        
        dynamic_target_value = None
        playbook_string = json.dumps(selected_pb)
        if "{{DYNAMIC_TARGET}}" in playbook_string:
            if args.target:
                dynamic_target_value = args.target.strip('"').strip("'")
            else:
                print(f"\n{Colors.WARNING}[*] This playbook requires a dynamic target.{Colors.ENDC}")
                raw_target = input(f"{Colors.BLUE}Enter Target File/Folder>{Colors.ENDC} ").strip()
                if raw_target.startswith('"') and raw_target.endswith('"'): raw_target = raw_target[1:-1]
                elif raw_target.startswith("'") and raw_target.endswith("'"): raw_target = raw_target[1:-1]
                dynamic_target_value = raw_target
                print(f"") 
        
        for index, step in enumerate(selected_pb.get('steps', [])):
            step_name = step.get('name', f"Step {index + 1}")
            target_tool = step.get('tool')
            raw_args = step.get('args', "")
            
            if dynamic_target_value and "{{DYNAMIC_TARGET}}" in raw_args:
                raw_args = raw_args.replace("{{DYNAMIC_TARGET}}", f'"{dynamic_target_value}"')
            
            print(f"\n{Colors.WARNING}[*] {step_name}{Colors.ENDC}")
            
            tool_path = get_tool_path(target_tool, registry)
            if not tool_path:
                print(f"{Colors.FAIL}[!] Error: Could not find tool '{target_tool}' in Commander Registry.{Colors.ENDC}")
                break
                
            full_tool_path = tool_path if os.path.isabs(tool_path) else os.path.join(platform_root, tool_path)
            print(f"{Colors.CYAN}[*] EXECUTING: {os.path.basename(tool_path)} {raw_args}{Colors.ENDC}\n")
            
            try:
                parsed_args = shlex.split(raw_args)
                if full_tool_path.endswith('.py'):
                    subprocess.run([sys.executable, full_tool_path] + parsed_args)
                else:
                    subprocess.run(["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", full_tool_path] + parsed_args)
            except Exception as e:
                print(f"{Colors.FAIL}[!] Playbook step failed: {os.path.basename(tool_path)} -> {e}{Colors.ENDC}")
                break 
                
        print(f"\n{Colors.GREEN}[+] Playbook Complete!{Colors.ENDC}")
        if not args.playbook or not os.environ.get("COMMANDER_BOOT"): input("\nPress Enter to return...")

    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}[*] Playbook execution aborted by user.{Colors.ENDC}")
        input(f"{Colors.BLUE}Press Enter to return to menu...{Colors.ENDC}")

if __name__ == "__main__":
    main()
