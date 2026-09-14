# TOOL_NAME: Platform_Health_Check
# DESC: Validates Commander registry, tool scripts, and playbook references for basic integrity.
# CATEGORY: System Utilities

import os
import sys
import json
import traceback
import subprocess

# Ensure core_lib is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

PYTHON_EXT = '.py'

def parse_tool_headers(filepath):
    """Parse basic tool headers (TOOL_NAME, DESC, CATEGORY, SOURCE_TYPES).
    These headers are treated as the on-disk "platform contract" for each tool
    and are compared against Commander_Registry.json to keep metadata aligned.
    """
    headers = {
        "TOOL_NAME": None,
        "DESC": None,
        "CATEGORY": None,
        "SOURCE_TYPES": None,
    }
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(40):
                raw_line = f.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line.startswith('#'):
                    continue
                upper = line.upper()
                for key in ["TOOL_NAME", "DESC", "CATEGORY", "SOURCE_TYPES"]:
                    token = f"# {key}:"
                    if token in upper:
                        value = line.split(':', 1)[1].strip()
                        headers[key] = value or None
    except Exception:
        # Fail soft; other checks will still run.
        return headers
    return headers

def load_registry(base_dir):
    """Load Commander_Registry.json if present."""
    reg_path = os.path.join(base_dir, 'Commander_Registry.json')
    if not os.path.exists(reg_path):
        print(f"{Colors.FAIL}[!] Commander_Registry.json not found at: {reg_path}{Colors.ENDC}")
        return []
    try:
        with open(reg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                print(f"{Colors.FAIL}[!] Registry file is not a list of tools.{Colors.ENDC}")
                return []
            return data
    except Exception as e:
        print(f"{Colors.FAIL}[!] Failed to parse Commander_Registry.json: {e}{Colors.ENDC}")
        return []

def build_registry_index(registry):
    """Build fast lookup dictionaries for registry by tool name and file_name."""
    by_name = {}
    by_file = {}
    for entry in registry:
        name = entry.get('name', '').lower()
        file_name = entry.get('file_name', '').lower()
        if name:
            by_name[name] = entry
        if file_name:
            by_file[file_name] = entry
    return by_name, by_file

def resolve_registry_path(base_dir, tool_path):
    if not tool_path:
        return tool_path
    if os.path.isabs(tool_path):
        return tool_path
    return os.path.join(base_dir, tool_path)

def parse_arguments_from_headers(filepath):
    """Lightweight parser for # ARG: headers in a tool script.
    Mirrors the header format used by tool_indexer.py so we can compare
    on-disk metadata with Commander_Registry.json without importing
    the indexer directly.
    """
    arguments = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(50):
                raw_line = f.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line.startswith('#'):
                    continue
                if '# ARG:' in line.upper():
                    raw_arg = line.split(':', 1)[1]
                    parts = [p.strip() for p in raw_arg.split('|')]
                    if len(parts) >= 4:
                        arguments.append({
                            "flag": parts[0],
                            "name": parts[1],
                            "description": parts[2],
                            "required": parts[3].lower() == 'true'
                        })
    except Exception:
        # Fail soft; other checks will still run.
        return []
    return arguments

def check_registry_paths(base_dir, registry):
    """Verify that every registry entry points to an existing file."""
    print(f"\n{Colors.HEADER}--- REGISTRY → FILESYSTEM CONSISTENCY ---{Colors.ENDC}")
    missing = 0
    for entry in registry:
        path = entry.get('path')
        resolved_path = resolve_registry_path(base_dir, path)
        name = entry.get('name', '<UNKNOWN>')
        if not resolved_path or not os.path.exists(resolved_path):
            print(f"{Colors.FAIL}[-] MISSING FILE:{Colors.ENDC} {name} -> {path}")
            missing += 1
    if missing == 0:
        print(f"{Colors.GREEN}[+] All registry paths resolve to existing files.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] Registry contains {missing} entries with missing files.{Colors.ENDC}")

def check_tools_covered_by_registry(base_dir, registry_by_file):
    """Ensure every Python tool under Tools/ (excluding core_lib, indexer, etc.) has a registry entry."""
    tools_dir = os.path.join(base_dir, 'Tools')
    print(f"\n{Colors.HEADER}--- FILESYSTEM → REGISTRY COVERAGE ---{Colors.ENDC}")
    uncovered = []
    for root, _, files in os.walk(tools_dir):
        # Skip internal libs and non-tool directories
        if 'core_lib' in root:
            continue
        for file in files:
            if not file.endswith(PYTHON_EXT):
                continue
            if file == '__init__.py' or file == 'tool_indexer.py':
                continue
            rel_file = file.lower()
            if rel_file not in registry_by_file:
                uncovered.append(os.path.join(root, file))
    if not uncovered:
        print(f"{Colors.GREEN}[+] All Python tools under Tools/ are present in Commander_Registry.json.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] The following Python files are NOT present in Commander_Registry.json:{Colors.ENDC}")
        for path in uncovered:
            print(f"  {Colors.WARNING}- {path}{Colors.ENDC}")

def check_playbooks_vs_registry(base_dir, registry_by_name):
    """Check that every tool referenced in a playbook exists in the registry."""
    playbooks_dir = os.path.join(base_dir, 'Playbooks')
    print(f"\n{Colors.HEADER}--- PLAYBOOK → REGISTRY CONSISTENCY ---{Colors.ENDC}")
    if not os.path.exists(playbooks_dir):
        print(f"{Colors.WARNING}[*] Playbooks directory not found; skipping playbook checks.{Colors.ENDC}")
        return
    missing_tools = 0
    for file in os.listdir(playbooks_dir):
        if not file.endswith('.json'):
            continue
        path = os.path.join(playbooks_dir, file)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"{Colors.FAIL}[-] Failed to parse playbook {file}: {e}{Colors.ENDC}")
            continue
        steps = data.get('steps', []) or []
        for step in steps:
            tool_name = (step.get('tool') or '').lower()
            if not tool_name:
                continue
            # Playbooks often refer to the registry 'name', not file_name
            if tool_name not in registry_by_name:
                print(f"{Colors.FAIL}[-] Playbook '{file}' references unknown tool: {tool_name}{Colors.ENDC}")
                missing_tools += 1
    if missing_tools == 0:
        print(f"{Colors.GREEN}[+] All playbook tool references resolve to registry entries.{Colors.ENDC}")

def check_tool_header_contract(base_dir, registry):
    """Enforce a minimal header contract for all registered tools."""
    print(f"\n{Colors.HEADER}--- TOOL HEADER CONTRACT ---{Colors.ENDC}")
    if not registry:
        print(f"{Colors.WARNING}[*] Registry not loaded; skipping tool header checks.{Colors.ENDC}")
        return
    
    allowed_categories = { (e.get('category') or 'Uncategorized').strip().lower() for e in registry }
    disallowed_sources = {'live_splunk_api', 'live_mde_api', 'live_ess_api', 'live_acas_api'}
    
    missing_headers = 0
    name_mismatches = 0
    category_mismatches = 0
    governance_warnings = 0
    
    for entry in registry:
        path = entry.get('path')
        resolved_path = resolve_registry_path(base_dir, path)
        if not resolved_path or not os.path.exists(resolved_path):
            continue
        headers = parse_tool_headers(resolved_path)
        tool_name_header = headers.get('TOOL_NAME')
        desc_header = headers.get('DESC')
        category_header = headers.get('CATEGORY')
        source_types_header = headers.get('SOURCE_TYPES')
        
        registry_name = entry.get('name', '').strip()
        registry_category = (entry.get('category') or 'Uncategorized').strip()
        
        if not tool_name_header or not desc_header or not category_header:
            missing_headers += 1
            print(f"{Colors.WARNING}[*] Missing required headers (TOOL_NAME/DESC/CATEGORY) in:{Colors.ENDC} {path}")
        else:
            if tool_name_header.strip().lower() != registry_name.lower():
                name_mismatches += 1
                print(f"{Colors.WARNING}[*] TOOL_NAME header does not match registry 'name':{Colors.ENDC} {path}")
            if category_header.strip().lower() != registry_category.lower():
                category_mismatches += 1
                print(f"{Colors.WARNING}[*] CATEGORY header does not match registry 'category':{Colors.ENDC} {path}")
            if category_header.strip().lower() not in allowed_categories:
                print(f"{Colors.WARNING}[*] CATEGORY header uses a value not seen in registry categories:{Colors.ENDC} {category_header}")
        
        if source_types_header:
            normalized = [s.strip().lower() for s in source_types_header.split(',') if s.strip()]
            bad = [s for s in normalized if s in disallowed_sources]
            if bad:
                governance_warnings += 1
                print(f"{Colors.WARNING}[*] SOURCE_TYPES for tool '{registry_name}' includes potential live integration markers:{Colors.ENDC} {', '.join(bad)}")
                
    if missing_headers == 0 and name_mismatches == 0 and category_mismatches == 0:
        print(f"{Colors.GREEN}[+] All registered tools satisfy the basic header contract.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] Header checks completed with {missing_headers} tools missing required headers, {name_mismatches} TOOL_NAME mismatches, and {category_mismatches} CATEGORY mismatches.{Colors.ENDC}")

def check_playbook_schema_and_governance(base_dir):
    """Validate playbook JSON schema and basic governance flags."""
    playbooks_dir = os.path.join(base_dir, 'Playbooks')
    print(f"\n{Colors.HEADER}--- PLAYBOOK SCHEMA & GOVERNANCE ---{Colors.ENDC}")
    if not os.path.exists(playbooks_dir):
        print(f"{Colors.WARNING}[*] Playbooks directory not found; skipping schema/governance checks.{Colors.ENDC}")
        return
        
    disallowed_sources = {'live_splunk_api', 'live_mde_api', 'live_ess_api', 'live_acas_api'}
    schema_issues = 0
    governance_warnings = 0
    
    for file in os.listdir(playbooks_dir):
        if not file.endswith('.json'):
            continue
        path = os.path.join(playbooks_dir, file)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            schema_issues += 1
            print(f"{Colors.FAIL}[-] Failed to parse playbook {file}: {e}{Colors.ENDC}")
            continue
            
        name = data.get('name')
        desc = data.get('description')
        steps = data.get('steps')
        
        if not isinstance(name, str) or not name.strip():
            schema_issues += 1
            print(f"{Colors.WARNING}[*] Playbook '{file}' is missing a valid 'name' field.{Colors.ENDC}")
        if not isinstance(desc, str) or not desc.strip():
            schema_issues += 1
            print(f"{Colors.WARNING}[*] Playbook '{file}' is missing a valid 'description' field.{Colors.ENDC}")
        if not isinstance(steps, list) or not steps:
            schema_issues += 1
            print(f"{Colors.WARNING}[*] Playbook '{file}' is missing a non-empty 'steps' list.{Colors.ENDC}")
        else:
            for idx, step in enumerate(steps):
                s_name = step.get('name')
                s_tool = step.get('tool')
                if not isinstance(s_name, str) or not s_name.strip():
                    schema_issues += 1
                    print(f"{Colors.WARNING}[*] Playbook '{file}' step {idx} missing valid 'name'.{Colors.ENDC}")
                if not isinstance(s_tool, str) or not s_tool.strip():
                    schema_issues += 1
                    print(f"{Colors.WARNING}[*] Playbook '{file}' step {idx} missing valid 'tool'.{Colors.ENDC}")

        enabled = data.get('enabled', None)
        source_types = data.get('source_types', None)
        normalized_sources = []
        
        if isinstance(source_types, list):
            normalized_sources = [str(s).strip().lower() for s in source_types if str(s).strip()]
        elif isinstance(source_types, str):
            normalized_sources = [s.strip().lower() for s in source_types.split(',') if s.strip()]
            
        if normalized_sources:
            bad = [s for s in normalized_sources if s in disallowed_sources]
            if bad:
                governance_warnings += 1
                print(f"{Colors.WARNING}[*] Playbook '{file}' source_types includes potential live integration markers:{Colors.ENDC} {', '.join(bad)}")

    if schema_issues == 0:
        print(f"{Colors.GREEN}[+] All playbooks satisfy the basic JSON schema contract.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] Playbook schema checks completed with {schema_issues} issues detected.{Colors.ENDC}")

def check_argument_metadata_consistency(base_dir, registry):
    """Compare # ARG: headers in tool scripts with registry 'arguments' metadata."""
    print(f"\n{Colors.HEADER}--- ARGUMENT METADATA CONSISTENCY ---{Colors.ENDC}")
    if not registry:
        print(f"{Colors.WARNING}[*] Registry not loaded; skipping argument metadata checks.{Colors.ENDC}")
        return
        
    mismatches = 0
    missing_in_registry = 0
    
    for entry in registry:
        path = entry.get('path')
        resolved_path = resolve_registry_path(base_dir, path)
        if not resolved_path or not os.path.exists(resolved_path):
            continue
        header_args = parse_arguments_from_headers(resolved_path)
        registry_args = entry.get('arguments', []) or []
        
        if header_args and not registry_args:
            print(f"{Colors.WARNING}[*] Tool has # ARG: headers but no 'arguments' in registry:{Colors.ENDC} {entry.get('name', path)}")
            missing_in_registry += 1
            continue
        if not header_args and registry_args:
            print(f"{Colors.WARNING}[*] Registry defines arguments but no # ARG: headers found in script:{Colors.ENDC} {entry.get('name', path)}")
            mismatches += 1
            continue
        if not header_args and not registry_args:
            continue
            
        max_len = max(len(header_args), len(registry_args))
        for idx in range(max_len):
            h = header_args[idx] if idx < len(header_args) else None
            r = registry_args[idx] if idx < len(registry_args) else None
            if h != r:
                mismatches += 1
                print(f"{Colors.FAIL}[-] Argument metadata mismatch in tool:{Colors.ENDC} {entry.get('name', path)} (index {idx})")
                break
                
    if mismatches == 0 and missing_in_registry == 0:
        print(f"{Colors.GREEN}[+] All # ARG: headers are consistent with registry 'arguments' metadata.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] Argument checks completed with {mismatches} mismatches and {missing_in_registry} tools missing registry arguments.{Colors.ENDC}")

def check_python_syntax(base_dir):
    """Attempt to compile all Python files under Tools/ to catch syntax errors early."""
    tools_dir = os.path.join(base_dir, 'Tools')
    print(f"\n{Colors.HEADER}--- PYTHON SYNTAX CHECK (Tools/) ---{Colors.ENDC}")
    syntax_errors = 0
    for root, _, files in os.walk(tools_dir):
        for file in files:
            if not file.endswith(PYTHON_EXT):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    source = f.read()
                compile(source, path, 'exec')
            except Exception:
                syntax_errors += 1
                print(f"{Colors.FAIL}[-] Syntax error in:{Colors.ENDC} {path}")
                traceback.print_exc(limit=1)
                
    if syntax_errors == 0:
        print(f"{Colors.GREEN}[+] No syntax errors detected in Tools/ Python files.{Colors.ENDC}")
    else:
        print(f"{Colors.WARNING}[*] Detected {syntax_errors} Python files with syntax errors.{Colors.ENDC}")

def main():
    base_dir = get_platform_root()
    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "=" * 80)
    print(" SOC PLATFORM HEALTH CHECK ".center(80))
    print("=" * 80 + f"{Colors.ENDC}")
    print(f"{Colors.CYAN}[*] Platform root resolved to:{Colors.ENDC} {base_dir}")

    # --- AUTO-RELOAD REGISTRY ---
    indexer_path = os.path.join(base_dir, 'Tools', 'tool_indexer', 'tool_indexer.py')
    if os.path.exists(indexer_path):
        print(f"\n{Colors.CYAN}[*] Auto-refreshing Commander Registry before health check...{Colors.ENDC}")
        try:
            subprocess.run([sys.executable, indexer_path], check=True, capture_output=True)
            print(f"{Colors.GREEN}[+] Registry successfully refreshed!{Colors.ENDC}")
        except subprocess.CalledProcessError as e:
            print(f"{Colors.FAIL}[!] Auto-refresh failed. Health check may be inaccurate.{Colors.ENDC}")
    # ----------------------------

    registry = load_registry(base_dir)
    if not registry:
        print(f"\n{Colors.WARNING}[*] Registry could not be loaded. Some checks will be skipped.{Colors.ENDC}")
        
    registry_by_name, registry_by_file = build_registry_index(registry) if registry else ({}, {})
    
    if registry:
        check_registry_paths(base_dir, registry)
        check_tools_covered_by_registry(base_dir, registry_by_file)
        check_playbooks_vs_registry(base_dir, registry_by_name)
        check_tool_header_contract(base_dir, registry)
        check_argument_metadata_consistency(base_dir, registry)
    else:
        print(f"\n{Colors.WARNING}[*] Skipping registry-based checks due to missing/invalid registry.{Colors.ENDC}")
        
    check_playbook_schema_and_governance(base_dir)
    check_python_syntax(base_dir)
    
    print(f"\n{Colors.CYAN}{Colors.BOLD}" + "-" * 80 + f"{Colors.ENDC}")
    print(f"{Colors.CYAN}[*] Health check complete. Review warnings/errors above for follow-up.{Colors.ENDC}")
    
    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return...{Colors.ENDC}")

if __name__ == "__main__":
    main()
