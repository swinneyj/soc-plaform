# TOOL_NAME: System_Downloads_Mapper
# DESC: Scans your actual Windows User Downloads folder and generates a complete file map saved to the SOC root.
# CATEGORY: System Utilities

import os
import datetime

def map_system_downloads():
    print("\n" + "="*55)
    print(" SYSTEM DOWNLOADS MAPPER ")
    print("="*55)

    # 1. Dynamically find the root of the CLEAN_SOC_PLATFORM for output
    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir

    # 2. Dynamically find the current Windows User's actual Downloads folder
    user_home = os.path.expanduser('~')
    target_dir = os.path.join(user_home, 'Downloads')
    
    if not os.path.exists(target_dir):
        print(f"[!] Could not locate the system Downloads folder at: {target_dir}")
        input("\nPress Enter to return to Commander...")
        return

    # 3. Set output path to the CLEAN_SOC_PLATFORM root
    out_path = os.path.join(platform_root, 'System_Downloads_Map.txt')

    print(f"[*] Scanning system directory: {target_dir}")
    print("[*] This may take a moment depending on the size of your Downloads folder...")
    
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"System Directory Map for: {target_dir}\n")
            f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*60 + "\n\n")
            
            for root, dirs, files in os.walk(target_dir):
                # Skip hidden directories and system caches to prevent map bloat
                dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
                
                # Calculate indentation
                level = root.replace(target_dir, '').count(os.sep)
                indent = ' ' * 4 * level
                folder_name = os.path.basename(root)
                
                if not folder_name:
                    folder_name = os.path.basename(target_dir)
                    
                f.write(f"{indent}[DIR] {folder_name}/\n")
                
                subindent = ' ' * 4 * (level + 1)
                for file in files:
                    f.write(f"{subindent}{file}\n")
        
        print("\n[+] Sweep complete!")
        print(f"[+] Total structural map saved to:\n    {out_path}")
        
    except PermissionError as e:
        print(f"\n[!] Permission Denied: {e}")
        print("[!] Note: Some system files in Downloads may be locked by Windows.")
    except Exception as e:
        print(f"\n[!] Error generating map: {e}")

    if not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    map_system_downloads()
