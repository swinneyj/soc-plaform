# TOOL_NAME: Dir_Mapper
# DESC: Generates a comprehensive, indented file and folder map of the Downloads directory and saves it to the root platform folder.
# CATEGORY: System Utilities

import os
import datetime

def map_directory():
    print("\n" + "="*50)
    print(" DIRECTORY MAPPER ")
    print("="*50)

    # 1. Dynamically find the root of the CLEAN_SOC_PLATFORM
    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    
    # Fallback just in case it's run from the root directly
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir

    # 2. Define the target directory to scan (assuming a 'Downloads' folder exists in root)
    target_dir = os.path.join(platform_root, 'Downloads')
    
    # Fallback: if 'Downloads' isn't in the root, it scans the platform root instead
    if not os.path.exists(target_dir):
        print(f"[*] 'Downloads' folder not found at {target_dir}. Scanning root instead...")
        target_dir = platform_root

    # 3. Set output path DIRECTLY to the CLEAN_SOC_PLATFORM root
    out_path = os.path.join(platform_root, 'Downloads_Map.txt')

    print(f"[*] Scanning directory: {target_dir}")
    
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"Directory Map for: {target_dir}\n")
            f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*50 + "\n\n")
            
            for root, dirs, files in os.walk(target_dir):
                # Skip hidden directories and caches
                dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
                
                level = root.replace(target_dir, '').count(os.sep)
                indent = ' ' * 4 * level
                folder_name = os.path.basename(root)
                
                if not folder_name:
                    folder_name = os.path.basename(target_dir)
                    
                f.write(f"{indent}[DIR] {folder_name}/\n")
                
                subindent = ' ' * 4 * (level + 1)
                for file in files:
                    f.write(f"{subindent}{file}\n")
        
        print("[+] Sweep complete!")
        print(f"[+] Total structural map saved to:\n    {out_path}")
        
    except Exception as e:
        print(f"[!] Error generating map: {e}")

    if not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    map_directory()
