# TOOL_NAME: Tool_Indexer
# DESC: Scans the toolkit and updates the Commander Registry.
# CATEGORY: Core Orchestration

import os
import json

def get_platform_root():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current_dir, '..', '..'))

def parse_tool_headers(filepath):
    base_dir = get_platform_root()
    relative_path = os.path.relpath(filepath, base_dir).replace('\\', '/')
    metadata = {
        "name": os.path.splitext(os.path.basename(filepath))[0], 
        "file_name": os.path.basename(filepath),
        "ext": os.path.splitext(filepath)[1],
        "path": relative_path,
        "category": "Uncategorized",
        "description": "No description provided."
    }
    arguments = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for i in range(50):
                raw_line = f.readline()
                if not raw_line: break
                line = raw_line.strip()
                if not line.startswith('#') and not line.startswith('\ufeff#'): continue
                
                check_line = line.upper()
                if '# TOOL_NAME:' in check_line: metadata['name'] = line.split(':', 1)[1].strip()
                elif '# DESC:' in check_line: metadata['description'] = line.split(':', 1)[1].strip()
                elif '# CATEGORY:' in check_line: metadata['category'] = line.split(':', 1)[1].strip()
                elif '# ARG:' in check_line:
                    raw_arg = line.split(':', 1)[1]
                    parts = [p.strip() for p in raw_arg.split('|')]
                    if len(parts) >= 4:
                        arguments.append({
                            "flag": parts[0], "name": parts[1], "description": parts[2], "required": parts[3].lower() == 'true'
                        })
    except Exception as e:
        print(f"[-] Error reading {filepath}: {e}")
        
    if arguments: metadata['arguments'] = arguments
    return metadata

def rebuild_registry():
    base_dir = get_platform_root()
    tools_dir = os.path.join(base_dir, 'Tools')
    registry_file = os.path.join(base_dir, 'Commander_Registry.json')
    
    print(f"[*] Indexer waking up. Root resolved to: {base_dir}")
    if not os.path.exists(tools_dir):
        print(f"[-] CRITICAL FAILURE: Tools directory not found at {tools_dir}")
        return
        
    print(f"[*] Scanning {tools_dir} for tools...\n")
    registry_data = []
    
    for root, _, files in os.walk(tools_dir):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                if 'core_lib' in root or file == '__init__.py' or 'AI_Knowledge_Base' in root: continue
                tool_profile = parse_tool_headers(filepath)
                registry_data.append(tool_profile)
                
    registry_data.sort(key=lambda x: (x.get('category', 'Uncategorized').upper(), x.get('name', '').upper()))
    
    try:
        with open(registry_file, 'w', encoding='utf-8') as f:
            json.dump(registry_data, f, indent=4)
        print(f"[+] Registry successfully rebuilt! Indexed {len(registry_data)} tools.")
    except Exception as e:
        print(f"[-] CRITICAL FAILURE: Could not write to registry file: {e}")

if __name__ == "__main__":
    rebuild_registry()
