# TOOL_NAME: Platform_Blueprint
# DESC: Scans the SOC platform to map all folder paths, files, metadata, and source code into a single document.
# CATEGORY: System Utilities

import os
import datetime

def generate_blueprint():
    print("\n" + "="*55)
    print(" PLATFORM BLUEPRINT GENERATOR ")
    print("="*55)

    # 1. Dynamically find the root of the CLEAN_SOC_PLATFORM
    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))

    # Fallback just in case it's run from the root directly
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir

    # Save the blueprint into the shared Data/Exports folder so it lives
    # alongside AI_Context_Export bundles for AI-assisted development.
    exports_dir = os.path.join(platform_root, 'Data', 'Exports')
    os.makedirs(exports_dir, exist_ok=True)
    out_path = os.path.join(exports_dir, 'SOC_Architecture_Blueprint.md')
    print(f"[*] Scanning environment: {platform_root}")

    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            # --- HEADER ---
            f.write("# SOC Platform Architecture Blueprint\n")
            f.write(f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Root Directory:** `{platform_root}`\n\n")
            f.write("---\n\n")

            # --- SECTION 1: DIRECTORY MAP ---
            f.write("## 1. Complete File and Folder Map\n```text\n")
            for root, dirs, files in os.walk(platform_root):
                if '__pycache__' in root:
                    continue
                level = root.replace(platform_root, '').count(os.sep)
                indent = ' ' * 4 * level
                folder_name = os.path.basename(root)
                if not folder_name:
                    folder_name = os.path.basename(platform_root)

                f.write(f"{indent}[DIR] {folder_name}/\n")
                subindent = ' ' * 4 * (level + 1)
                for file in files:
                    f.write(f"{subindent}{file}\n")
            f.write("```\n\n---\n\n")

            # --- SECTION 2: TOOL CONFIGURATIONS ---
            f.write("## 2. Tool Configurations & Metadata\n\n")
            for root, _, files in os.walk(platform_root):
                if '__pycache__' in root:
                    continue
                for file in files:
                    if file.endswith(('.py', '.ps1')):
                        file_path = os.path.join(root, file)
                        # Default fallback values
                        tool_name = file
                        desc = "No description provided."
                        cat = "Uncategorized"

                        # Extract the configuration headers
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as sf:
                                for _ in range(15):
                                    line = sf.readline().strip()
                                    if line.startswith('# TOOL_NAME:'): 
                                        tool_name = line.split(':', 1)[1].strip()
                                    if line.startswith('# DESC:'): 
                                        desc = line.split(':', 1)[1].strip()
                                    if line.startswith('# CATEGORY:'): 
                                        cat = line.split(':', 1)[1].strip()
                        except Exception:
                            pass

                        # Write the tool profile
                        rel_path = file_path.replace(platform_root, '')
                        f.write(f"### {tool_name}\n")
                        f.write(f"* **Category:** {cat}\n")
                        f.write(f"* **Description:** {desc}\n")
                        f.write(f"* **File Name:** `{file}`\n")
                        f.write(f"* **Relative Path:** `{rel_path}`\n\n")
            
            f.write("---\n\n")
            
            # --- SECTION 3: SOURCE CODE (NEW) ---
            f.write("## 3. Tool Source Codes\n\n")
            for root, _, files in os.walk(platform_root):
                if '__pycache__' in root:
                    continue
                for file in files:
                    if file.endswith(('.py', '.json')):
                        file_path = os.path.join(root, file)
                        rel_path = file_path.replace(platform_root, '')
                        lang = "python" if file.endswith('.py') else "json"
                        
                        f.write(f"### {file}\n")
                        f.write(f"**Path:** `{rel_path}`\n\n")
                        f.write(f"```{lang}\n")
                        try:
                            with open(file_path, 'r', encoding='utf-8') as sf:
                                f.write(sf.read())
                        except Exception as e:
                            f.write(f"# Error reading file: {e}\n")
                        f.write("\n```\n\n---\n\n")

        print(f"[+] Blueprint successfully generated!")
        print(f"    -> {out_path}")

    except Exception as e:
        print(f"\n[!] Error generating blueprint: {e}")

    if not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    generate_blueprint()
