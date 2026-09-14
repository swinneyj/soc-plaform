# TOOL_NAME: AI_Context_Exporter
# DESC: Bundles source code, configs, and directory structure into a single text file for AI context sharing.
# CATEGORY: System Utilities

import os
import sys
import datetime

# Import shared core library
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from core_lib.utils import get_platform_root

def main():
    platform_root = get_platform_root()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    output_filename = f"AI_Context_Export_{timestamp}.txt"
    # Save context exports into Data/Exports so they colocate with
    # SOC_Architecture_Blueprint.md for easier AI ingestion.
    exports_dir = os.path.join(platform_root, 'Data', 'Exports')
    os.makedirs(exports_dir, exist_ok=True)
    output_path = os.path.join(exports_dir, output_filename)
    
    # Target extensions to include in the bundle
    target_extensions = ['.py', '.json', '.md', '.bat', '.ps1']
    # Folders to explicitly ignore to prevent bloat
    ignore_dirs = ['__pycache__', '.git', 'Data', 'Archive', 'Consolidated_Paperwork', 'SOC_Archive', 'venv']

    print("==================================================")
    print(" AI CONTEXT EXPORTER")
    print("==================================================")
    print(f"[*] Bundling context from: {platform_root}")
    
    with open(output_path, 'w', encoding='utf-8') as outfile:
        # Write a header for the AI
        outfile.write("SOC PLATFORM SOURCE BUNDLE\n")
        outfile.write(f"Generated: {timestamp}\n")
        outfile.write("==================================================\n\n")
        
        # Generate a directory tree map at the top of the file
        outfile.write("DIRECTORY STRUCTURE:\n")
        for root, dirs, files in os.walk(platform_root):
            # Modify the dirs list in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            # Calculate indentation based on depth
            level = root.replace(platform_root, '').count(os.sep)
            indent = ' ' * 4 * level
            outfile.write(f"{indent}{os.path.basename(root)}/\n")
            
            subindent = ' ' * 4 * (level + 1)
            for f in files:
                if any(f.endswith(ext) for ext in target_extensions) or f == 'requirements.txt':
                    outfile.write(f"{subindent}{f}\n")
        
        outfile.write("\n==================================================\n\n")

        # Gather and append file contents
        for root, dirs, files in os.walk(platform_root):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if any(file.endswith(ext) for ext in target_extensions) or file == 'requirements.txt':
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, platform_root)
                    
                    # Write clear file delimiters for the AI to parse
                    outfile.write(f"\n{'='*80}\n")
                    outfile.write(f"FILE: {rel_path}\n")
                    outfile.write(f"{'='*80}\n\n")
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8') as infile:
                            outfile.write(infile.read())
                        print(f"  [+] Appended: {rel_path}")
                    except Exception as e:
                        outfile.write(f"[!] Could not read file: {e}\n")
                        print(f"  [!] Failed to read {rel_path}: {e}")
                    
                    outfile.write("\n\n")

    print(f"\n[+] AI Context Bundle Generated!")
    print(f"[+] Please upload this file to your AI assistant: {output_filename}")

if __name__ == "__main__":
    main()
