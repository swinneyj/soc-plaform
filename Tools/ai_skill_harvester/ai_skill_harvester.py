# TOOL_NAME: AI_Skill_Harvester
# DESC: Scans the user Downloads directory for scattered AI SKILL.md files, centralizes them, and generates an index.
# CATEGORY: System Utilities

import os
import sys
import shutil

# Dynamically add the Tools directory to the path so we can load core_lib
current_dir = os.path.dirname(os.path.abspath(__file__))
tools_dir = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(tools_dir)

from core_lib.utils import Colors, clear_screen, get_platform_root

def harvest_skills():
    """Finds AI Skill and Memory files and centralizes them."""
    platform_root = get_platform_root()
    # Assume Downloads is the parent of the SOC Toolkit
    downloads_dir = os.path.abspath(os.path.join(platform_root, '..')) 
    
    # Create the Knowledge Base directory
    kb_dir = os.path.join(platform_root, 'Data', 'AI_Knowledge_Base')
    os.makedirs(kb_dir, exist_ok=True)
    
    print(f"\n{Colors.CYAN}[*] Initiating AI Skill Harvest...{Colors.ENDC}")
    print(f"[*] Scanning directory: {downloads_dir}\n")
    
    harvested_count = 0
    index_data = []
    
    # Keywords to look for in the filename
    target_keywords = ['skill', 'memory']
    # Valid extensions to pull
    valid_extensions = ['.md', '.txt', '.json']

    # Walk the Downloads directory
    for root, dirs, files in os.walk(downloads_dir):
        # Skip the SOC Toolkit itself to avoid endless loops
        if 'SOC Toolkit' in root:
            continue
            
        for file in files:
            file_lower = file.lower()
            
            # Check if it's a skill/memory file and a valid text format
            if any(keyword in file_lower for keyword in target_keywords) and any(file_lower.endswith(ext) for ext in valid_extensions):
                
                # If the file is just named "skill.md", prepend the folder name so they don't overwrite each other
                if file_lower == 'skill.md' or file_lower == 'memory.md' or file_lower == 'skill.txt':
                    parent_folder = os.path.basename(root)
                    new_filename = f"{parent_folder}_{file}"
                else:
                    new_filename = file
                    
                src_path = os.path.join(root, file)
                dst_path = os.path.join(kb_dir, new_filename)
                
                try:
                    shutil.copy2(src_path, dst_path)
                    print(f"  {Colors.GREEN}[+]{Colors.ENDC} Harvested: {new_filename}")
                    
                    # Read the first few lines to generate a preview for the index
                    preview = "No description available."
                    try:
                        with open(src_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = [line.strip() for line in f.readlines() if line.strip() and not line.startswith('#')]
                            if lines:
                                preview = lines[0][:80] + "..." if len(lines[0]) > 80 else lines[0]
                    except:
                        pass # Ignore read errors for binary/weird files
                            
                    index_data.append(f"- **{new_filename}**: {preview}")
                    harvested_count += 1
                except Exception as e:
                    print(f"  {Colors.FAIL}[-]{Colors.ENDC} Failed to copy {src_path}: {e}")

    # Generate the Index File
    if harvested_count > 0:
        index_path = os.path.join(kb_dir, 'Skill_Index.md')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write("# Centralized AI Skill & Memory Index\n\n")
            f.write("This document indexes all harvested AI context files available to the SOC Commander platform.\n\n")
            f.write("\n".join(index_data))
            
        print(f"\n{Colors.GREEN}[+] Harvest Complete!{Colors.ENDC}")
        print(f"[*] Successfully centralized {harvested_count} files into /Data/AI_Knowledge_Base/")
        print(f"[*] Generated index: {index_path}")
    else:
        print(f"\n{Colors.WARNING}[!] No Skill or Memory files found in the Downloads directory.{Colors.ENDC}")

def main():
    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "="*80)
    print(" AI SKILL HARVESTER ".center(80))
    print("="*80 + f"{Colors.ENDC}")
    
    harvest_skills()
    
    if not os.environ.get("COMMANDER_BOOT"):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")

if __name__ == "__main__":
    main()
