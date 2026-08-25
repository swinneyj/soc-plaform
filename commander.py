# TOOL_NAME: SOC_Commander
# DESC: The central orchestrator for the SOC platform. Manages tool execution, registry indexing, and user interface.
# CATEGORY: Core Orchestration

import os, sys, json, subprocess, shlex, datetime
from collections import defaultdict

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Tools'))
from core_lib.utils import Colors, clear_screen, get_platform_root
from core_lib.smart_router import detect_file_type

TERMINAL_WIDTH = 100


def print_welcome_banner():
    """One-time quick tour shown when Commander first starts."""
    clear_screen()
    print(f"\n{Colors.CYAN}{Colors.BOLD}" + "=" * TERMINAL_WIDTH)
    print(" WELCOME TO THE SOC TOOLKIT COMMANDER ".center(TERMINAL_WIDTH))
    print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")

    print(f"\n{Colors.HEADER}Quick tour of what this platform can do:{Colors.ENDC}")
    print(f" {Colors.GREEN}- Playbooks:{Colors.ENDC} Run automated triage and response workflows for common incidents.")
    print(f" {Colors.GREEN}- Smart Ingestion:{Colors.ENDC} Drop a file or paste text and let the toolkit recommend the right parser.")
    print(f" {Colors.GREEN}- Text Sanitization:{Colors.ENDC} Clean logs/notes so they are safe to share with public AI tools.")
    print(f" {Colors.GREEN}- Maintenance & Context:{Colors.ENDC} Clean the workspace, back up the platform, and export AI-ready context bundles.")

    print(f"\n{Colors.CYAN}If you're not sure where to start, just type 'help' when you see the commander> prompt.{Colors.ENDC}")
    input(f"\n{Colors.BLUE}Press Enter to continue into the guided assistant...{Colors.ENDC}")


def log_usage(base_dir, action, detail=""):
    """Minimal usage logging for key Commander flows (helper, sanitizer, etc.)."""
    try:
        logs_dir = os.path.join(base_dir, 'Data', 'Logs')
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, 'Commander_Usage.log')
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] {action}"
        if detail:
            line += f" | {detail}"
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        # Logging should never break core functionality; fail silently.
        pass

def load_registry(base_dir):
    reg_path = os.path.join(base_dir, 'Commander_Registry.json')
    if not os.path.exists(reg_path): return []
    with open(reg_path, 'r', encoding='utf-8') as f: 
        registry = json.load(f)
        registry.sort(key=lambda x: (x.get('category', 'Uncategorized').upper(), x.get('name', '').upper()))
        return registry

def resolve_registry_path(tool_path):
    if not tool_path:
        return tool_path
    if os.path.isabs(tool_path):
        return tool_path
    return os.path.join(get_platform_root(), tool_path)

def execute_tool(target, args_string):
    """Executes a tool from the registry."""
    print(f"\n{Colors.WARNING}[*] Launching {target['name']}...{Colors.ENDC}")
    parsed_args = shlex.split(args_string)
    
    abort_launch = False
    if not parsed_args and target.get('arguments'):
        print(f"{Colors.CYAN}[*] Entering interactive prompt mode for missing arguments...{Colors.ENDC}")
        print(f"{Colors.CYAN}[*] TIP: You can drag and drop a file here to auto-fill its path.{Colors.ENDC}")
        for arg in target['arguments']:
            prompt_text = f"  Enter {arg.get('name', 'value')} ({arg.get('description', '')}): "
            val = input(f"{Colors.BLUE}{prompt_text}{Colors.ENDC}").strip()
            
            if val.startswith('"') and val.endswith('"'): val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"): val = val[1:-1]
            
            if val:
                parsed_args.extend([arg.get('flag', ''), val])
            elif arg.get('required', False):
                print(f"{Colors.FAIL}[!] Required argument '{arg.get('name')}' missing. Aborting.{Colors.ENDC}")
                abort_launch = True
                break

    if not abort_launch:
        try:
            parsed_args = [arg for arg in parsed_args if arg]
            resolved_path = resolve_registry_path(target['path'])
            if target.get('ext') == '.py' or resolved_path.endswith('.py'):
                subprocess.run([sys.executable, resolved_path] + parsed_args)
            else: 
                subprocess.run(["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", resolved_path] + parsed_args)
        except Exception as e: 
            print(f"{Colors.FAIL}[!] Error: {e}{Colors.ENDC}")
    
    input(f"\n{Colors.CYAN}[*] Execution finished. Press Enter to return...{Colors.ENDC}")

def smart_ingestion_menu(registry):
    """The Auto-Detect Engine Menu."""
    clear_screen()
    print(f"\n{Colors.CYAN}{Colors.BOLD}" + "=" * TERMINAL_WIDTH)
    print(" SMART INGESTION ENGINE ".center(TERMINAL_WIDTH))
    print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")
    print(f"\n{Colors.WARNING}[*] Drop a file here, and Commander will analyze its contents to recommend tools.{Colors.ENDC}")
    print(f"{Colors.CYAN}[*] TIP: Type 'paste' to paste raw text instead of a file path.{Colors.ENDC}")

    raw_path = input(f"\n{Colors.BLUE}Drop Evidence File>{Colors.ENDC} ").strip()
    if not raw_path or raw_path.lower() in ['back', 'exit', '0']:
        return

    # NEW: support a paste-mode where the user pastes text instead of providing a file path
    if raw_path.lower() in ['paste', 'p', 'text']:
        print(f"\n{Colors.CYAN}[*] Paste your text below. Type 'END' on a line by itself to finish.{Colors.ENDC}")
        print("-" * TERMINAL_WIDTH)
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip().upper() == 'END':
                break
            lines.append(line)

        pasted_text = "\n".join(lines).strip()
        if not pasted_text:
            print(f"\n{Colors.WARNING}[*] No text provided. Returning to main menu.{Colors.ENDC}")
            input("\nPress Enter to return...")
            return

        base_dir = get_platform_root()
        active_dir = os.path.join(base_dir, 'Data', 'Active_Workspace')
        os.makedirs(active_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filepath = os.path.join(active_dir, f"SmartIngest_Pasted_{timestamp}.txt")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(pasted_text)
        print(f"\n{Colors.CYAN}[*] Pasted text saved to: {filepath}{Colors.ENDC}")
    else:
        filepath = raw_path.strip('"').strip("'")

        if not os.path.exists(filepath):
            print(f"{Colors.FAIL}[!] File not found: {filepath}{Colors.ENDC}")
            input("\nPress Enter to return...")
            return
        
    print(f"\n{Colors.CYAN}[*] Analyzing file fingerprint...{Colors.ENDC}")
    recommended_tool_names = detect_file_type(filepath, silent=False)
    
    if not recommended_tool_names:
        print(f"\n{Colors.WARNING}[-] Could not determine file context. No specific tools recommended.{Colors.ENDC}")
        input("\nPress Enter to return...")
        return
        
    print(f"\n{Colors.HEADER}--- RECOMMENDED TOOLS FOR THIS DATA ---{Colors.ENDC}")
    
    recommended_registry_items = []
    for tool_name in recommended_tool_names:
        target = next((t for t in registry if t['name'].lower() == tool_name.lower()), None)
        if target: recommended_registry_items.append(target)
        
    for idx, target in enumerate(recommended_registry_items):
        name = target['name']
        desc = target.get('description', 'No description provided.')
        print(f" {Colors.GREEN}[{idx+1:2d}]{Colors.ENDC} {name:<30} | {desc}")
        
    print(f"\n{Colors.CYAN}" + "-" * TERMINAL_WIDTH + f"{Colors.ENDC}")
    choice = input(f"\n{Colors.BLUE}Select a recommended tool to run (or '0' to cancel):{Colors.ENDC} ").strip()
    
    if choice.isdigit() and 1 <= int(choice) <= len(recommended_registry_items):
        selected_tool = recommended_registry_items[int(choice)-1]
        
        args_string = ""
        if selected_tool.get('arguments'):
            for arg in selected_tool['arguments']:
                if 'file' in arg.get('name', '').lower() or 'target' in arg.get('name', '').lower() or 'input' in arg.get('name', '').lower() or 'evidence' in arg.get('name', '').lower():
                    args_string = f"{arg['flag']} \"{filepath}\""
                    break
                    
        execute_tool(selected_tool, args_string)
    else:
        print(f"{Colors.WARNING}[*] Analysis cancelled.{Colors.ENDC}")

def manual_tools_menu(registry):
    """Sub-menu for running individual scripts manually."""
    while True:
        try:
            clear_screen()
            print(f"\n{Colors.CYAN}{Colors.BOLD}" + "=" * TERMINAL_WIDTH)
            print(" MANUAL INVESTIGATION TOOLS ".center(TERMINAL_WIDTH))
            print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")

            # Only show analyst-facing tools here; hide core maintenance/internal tools
            internal_only = {
                'Playbook_Runner',
                'Maintenance_Suite',
                'Platform_Blueprint',
                'Platform_Health_Check',
                'System_Downloads_Mapper',
                'Tool_Packager',
                'Workspace_Cleaner',
                'tool_indexer',
            }

            filtered_registry = [t for t in registry if t['name'] not in internal_only]
            
            grouped = defaultdict(list)
            for i, t in enumerate(filtered_registry): 
                grouped[t.get('category', 'Uncategorized')].append((i+1, t))
            
            for cat in sorted(grouped.keys()):
                print(f"\n{Colors.HEADER}--- {cat.upper()} ---{Colors.ENDC}")
                for idx, target in grouped[cat]:
                    name = target['name']
                    desc = target.get('description', 'No description provided.')
                    print(f" {Colors.GREEN}[{idx:2d}]{Colors.ENDC} {name:<30} | {desc}")
                    
            print(f"\n{Colors.CYAN}" + "-" * TERMINAL_WIDTH + f"{Colors.ENDC}")
            print("Commands: [ID] or [Name] | 'info [Name]' | 'edit [Name]' | 'search [keyword]' | 'back'")
            
            raw_cmd = input(f"\n{Colors.BLUE}tools>{Colors.ENDC} ").strip()
            cmd_lower = raw_cmd.lower()
            
            if cmd_lower in ['back', 'exit', 'quit', '0']:
                break
                
            def find_tool(identifier):
                if identifier.isdigit() and 1 <= int(identifier) <= len(filtered_registry):
                    return filtered_registry[int(identifier)-1]
                return next((t for t in filtered_registry if t['name'].lower() == identifier.lower()), None)
                
            if cmd_lower.startswith('info '):
                identifier = raw_cmd.split(' ', 1)[1]
                target = find_tool(identifier)
                if target:
                    print(f"\n{Colors.CYAN}" + "=" * TERMINAL_WIDTH)
                    print(f" TOOL INFO: {target['name'].upper()}")
                    print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")
                    print(f"Category:    {target['category']}")
                    print(f"File Name:   {target.get('file_name', 'Unknown')}")
                    print(f"Location:    {resolve_registry_path(target['path'])}")
                    print(f"Description: {target.get('description', 'No description provided.')}")
                    print(f"{Colors.CYAN}" + "=" * TERMINAL_WIDTH + f"{Colors.ENDC}")
                else:
                    print(f"\n{Colors.FAIL}[!] Tool '{identifier}' not found.{Colors.ENDC}")
                input("\nPress Enter to return...")
                
            elif cmd_lower.startswith('edit '):
                identifier = raw_cmd.split(' ', 1)[1]
                target = find_tool(identifier)
                if target:
                    print(f"\n{Colors.WARNING}[*] Opening {target.get('file_name', 'file')} in Notepad...{Colors.ENDC}")
                    try: subprocess.Popen(['notepad.exe', resolve_registry_path(target['path'])])
                    except Exception as e: print(f"{Colors.FAIL}[!] Could not open: {e}{Colors.ENDC}")
                else:
                    print(f"\n{Colors.FAIL}[!] Tool '{identifier}' not found.{Colors.ENDC}")
                input("\nPress Enter to return...")
                
            elif cmd_lower.startswith('search '):
                keyword = cmd_lower.split(' ', 1)[1]
                print(f"\n{Colors.CYAN}[*] Searching registry for: '{keyword}'...{Colors.ENDC}")
                found = False
                for i, target in enumerate(filtered_registry):
                    if keyword in target['name'].lower() or keyword in target.get('description', '').lower() or keyword in target.get('category', '').lower():
                        print(f"  {Colors.GREEN}[+]{Colors.ENDC} {target['name']:<30} | {target.get('category', 'Uncategorized')}")
                        found = True
                if not found: print(f"  {Colors.FAIL}[-] No matching tools found.{Colors.ENDC}")
                input("\nPress Enter to return...")

            else:
                parts = raw_cmd.split(' ', 1)
                identifier = parts[0]
                args_string = parts[1] if len(parts) > 1 else ""
                
                target = find_tool(identifier)
                if target:
                    execute_tool(target, args_string)
                elif raw_cmd: 
                    print(f"{Colors.FAIL}[!] Invalid selection or command.{Colors.ENDC}")
                    input("\nPress Enter to return...")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.WARNING}[*] Operation aborted by user. Returning to tools menu...{Colors.ENDC}")
            input(f"{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")
            continue
        except Exception as e:
            print(f"\n{Colors.FAIL}[!] Unexpected error: {e}{Colors.ENDC}")
            input("Press Enter to return...")


def guided_helper(base_dir, registry):
    """Interactive helper to steer new users toward the right workflow."""
    while True:
        try:
            clear_screen()
            print(f"\n{Colors.CYAN}{Colors.BOLD}" + "=" * TERMINAL_WIDTH)
            print(" GUIDED ASSISTANT - WHAT DO YOU WANT TO DO? ".center(TERMINAL_WIDTH))
            print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")

            print(f"\n{Colors.HEADER}Pick the statement that fits you best:{Colors.ENDC}")
            print(f" {Colors.GREEN}[1]{Colors.ENDC} I have text/logs and want AI help after sanitizing them")
            print(f" {Colors.GREEN}[2]{Colors.ENDC} I have a file/export and want the toolkit to analyze it")
            print(f" {Colors.GREEN}[3]{Colors.ENDC} I want to run automated triage/playbooks")
            print(f" {Colors.GREEN}[4]{Colors.ENDC} I want to maintain or prep the platform (cleanup/backup/AI context)")
            print(f" {Colors.GREEN}[5]{Colors.ENDC} I want to browse and run individual tools")
            print(f" {Colors.GREEN}[0]{Colors.ENDC} Return to Commander main menu")

            choice = input(f"\n{Colors.BLUE}helper>{Colors.ENDC} ").strip().lower()

            if choice in ['0', 'back', 'exit', 'quit']:
                log_usage(base_dir, "helper_exit")
                return

            # 1) Text/logs -> Text Sanitizer Pipeline (Commander option 5 under the hood)
            if choice == '1':
                base_text_tool = os.path.join(base_dir, 'Tools', 'text_sanitizer_pipeline', 'text_sanitizer_pipeline.py')
                if os.path.exists(base_text_tool):
                    log_usage(base_dir, "helper_choice", "text_sanitization")
                    print(f"\n{Colors.CYAN}[*] Launching Text Sanitizer Pipeline...{Colors.ENDC}")
                    print(f"{Colors.CYAN}[*] You can paste text (END to finish) or provide a file path when prompted.{Colors.ENDC}")
                    try:
                        subprocess.run([sys.executable, base_text_tool])
                    except Exception as e:
                        print(f"{Colors.FAIL}[!] Error running Text_Sanitizer_Pipeline: {e}{Colors.ENDC}")
                        input("\nPress Enter to return to the helper...")
                else:
                    print(f"\n{Colors.FAIL}[!] Text_Sanitizer_Pipeline not found at {base_text_tool}.{Colors.ENDC}")
                    input("\nPress Enter to return to the helper...")

            # 2) File/export -> Smart Ingestion (Commander option 4 under the hood)
            elif choice == '2':
                log_usage(base_dir, "helper_choice", "smart_ingestion")
                smart_ingestion_menu(registry)

            # 3) Automated triage / playbooks
            elif choice == '3':
                pb_tool = next((t for t in registry if t['name'] == 'Playbook_Runner'), None)
                if pb_tool:
                    log_usage(base_dir, "helper_choice", "playbook_runner")
                    print(f"\n{Colors.CYAN}[*] Launching Playbook Runner for automated triage workflows...{Colors.ENDC}")
                    subprocess.run([sys.executable, resolve_registry_path(pb_tool['path'])])
                else:
                    print(f"\n{Colors.FAIL}[!] Playbook_Runner not found in registry. From Commander, run 'reload' to rebuild the registry.{Colors.ENDC}")
                    input("\nPress Enter to return to the helper...")

            # 4) Platform maintenance / AI context
            elif choice == '4':
                maint_tool = next((t for t in registry if t['name'] == 'Maintenance_Suite'), None)
                if maint_tool:
                    log_usage(base_dir, "helper_choice", "maintenance_suite")
                    print(f"\n{Colors.CYAN}[*] Launching Maintenance Suite (cleanup, backup, AI context exports)...{Colors.ENDC}")
                    subprocess.run([sys.executable, resolve_registry_path(maint_tool['path'])])
                else:
                    print(f"\n{Colors.FAIL}[!] Maintenance_Suite not found in registry. Run 'reload' from Commander.{Colors.ENDC}")
                    input("\nPress Enter to return to the helper...")

            # 5) Browse and run individual tools
            elif choice == '5':
                log_usage(base_dir, "helper_choice", "manual_tools_menu")
                manual_tools_menu(registry)

            else:
                print(f"\n{Colors.FAIL}[!] Invalid selection.{Colors.ENDC}")
                input("\nPress Enter to try again...")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.WARNING}[*] Helper aborted by user. Returning to Commander...{Colors.ENDC}")
            input(f"{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")
            return

def main():
    base_dir = get_platform_root()
    os.system('') # Enable ANSI escape sequences
    first_run = True

    while True:
        try:
            registry = load_registry(base_dir)
            # On first launch, walk the user through the guided helper
            # so new users can describe their goal in plain language.
            if first_run:
                print_welcome_banner()
                guided_helper(base_dir, registry)
                first_run = False

            clear_screen()
            
            print(f"\n{Colors.CYAN}{Colors.BOLD}" + "=" * TERMINAL_WIDTH)
            print(" SOC COMMANDER - MAIN ORCHESTRATION MENU ".center(TERMINAL_WIDTH))
            print("=" * TERMINAL_WIDTH + f"{Colors.ENDC}")
            
            print(f"\n{Colors.HEADER}--- SELECT OPERATIONAL MODE ---{Colors.ENDC}")
            print(f" {Colors.GREEN}[1]{Colors.ENDC} Automated Triage & Playbooks      | Run zero-touch incident response workflows")
            print(f" {Colors.GREEN}[2]{Colors.ENDC} Manual Investigation Tools        | Run individual data processors and scrapers")
            print(f" {Colors.GREEN}[3]{Colors.ENDC} System Maintenance & Admin        | Clean workspace, export AI context, backup")
            print(f" {Colors.GREEN}[4]{Colors.ENDC} Smart Ingestion (Auto-Detect)     | Drop a file, and Commander will recommend the tool")
            print(f" {Colors.GREEN}[5]{Colors.ENDC} Text Cleaning & Sanitization      | Paste or drag-drop text file, sanitize, and open in Notepad")
            print(f" {Colors.GREEN}[0]{Colors.ENDC} Exit Platform")
            
            print(f"\n{Colors.CYAN}" + "-" * TERMINAL_WIDTH + f"{Colors.ENDC}")
            print("System Commands: 'reload' (Rebuild Registry) | 'clear' | 'edit commander'")
            print("Guided Help: type 'help' for an interactive assistant if you're not sure where to start")
            
            raw_cmd = input(f"\n{Colors.BLUE}commander>{Colors.ENDC} ").strip().lower()
            
            if raw_cmd in ['0', 'exit', 'quit']:
                print(f"\n{Colors.WARNING}[*] Exiting SOC Commander.{Colors.ENDC}")
                break
            elif raw_cmd in ['clear', 'cls']:
                continue
            elif raw_cmd == 'reload':
                print(f"\n{Colors.WARNING}[*] Rebuilding registry...{Colors.ENDC}")
                indexer = os.path.join(base_dir, 'Tools', 'tool_indexer', 'tool_indexer.py') 
                if os.path.exists(indexer): 
                    subprocess.run([sys.executable, indexer])
                else:
                    print(f"{Colors.FAIL}[!] Indexer script not found at {indexer}{Colors.ENDC}")
                input("\nPress Enter to return...")
                continue
            elif raw_cmd == 'edit commander':
                print(f"\n{Colors.WARNING}[*] Opening commander.py in Notepad...{Colors.ENDC}")
                subprocess.Popen(['notepad.exe', os.path.abspath(__file__)])
                continue
            elif raw_cmd == 'help':
                log_usage(base_dir, "command", "help")
                guided_helper(base_dir, registry)
                continue
            elif raw_cmd in ['5', 'text', 'sanitize', 'sanitize text']:
                # Text cleaning workflow: launch the Text_Sanitizer_Pipeline in interactive mode
                # so the user can choose Paste (END to finish) or drag-drop/file-path input.
                base_text_tool = os.path.join(base_dir, 'Tools', 'text_sanitizer_pipeline', 'text_sanitizer_pipeline.py')
                if os.path.exists(base_text_tool):
                    try:
                        log_usage(base_dir, "command", "text_sanitizer")
                        subprocess.run([sys.executable, base_text_tool])
                    except Exception as e:
                        print(f"{Colors.FAIL}[!] Error running Text_Sanitizer_Pipeline: {e}{Colors.ENDC}")
                        input("\nPress Enter to return...")
                else:
                    print(f"{Colors.FAIL}[!] Text_Sanitizer_Pipeline not found at {base_text_tool}.{Colors.ENDC}")
                    input("\nPress Enter to return...")
                
            if raw_cmd == '1':
                pb_tool = next((t for t in registry if t['name'] == 'Playbook_Runner'), None)
                if pb_tool: subprocess.run([sys.executable, resolve_registry_path(pb_tool['path'])])
                else: 
                    print(f"{Colors.FAIL}[!] Playbook_Runner not found in registry. Run 'reload'.{Colors.ENDC}")
                    input("\nPress Enter to return...")
                    
            elif raw_cmd == '2':
                manual_tools_menu(registry)
                
            elif raw_cmd == '3':
                maint_tool = next((t for t in registry if t['name'] == 'Maintenance_Suite'), None)
                if maint_tool: subprocess.run([sys.executable, resolve_registry_path(maint_tool['path'])])
                else:
                    print(f"{Colors.FAIL}[!] Maintenance_Suite not found in registry. Run 'reload'.{Colors.ENDC}")
                    input("\nPress Enter to return...")
                    
            elif raw_cmd == '4':
                smart_ingestion_menu(registry)
                
            else:
                print(f"{Colors.FAIL}[!] Invalid selection.{Colors.ENDC}")
                input("\nPress Enter to return...")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.WARNING}[*] Operation aborted by user. Returning to main menu...{Colors.ENDC}")
            input(f"{Colors.BLUE}Press Enter to continue...{Colors.ENDC}")
            continue
        except Exception as e:
            print(f"\n{Colors.FAIL}[!] Unexpected orchestrator error: {e}{Colors.ENDC}")
            input("Press Enter to continue...")

if __name__ == "__main__": 
    main()
