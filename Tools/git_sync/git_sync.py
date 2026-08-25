# TOOL_NAME: Git_Sync_Manager
# DESC: Interactive Git utility to safely pull updates, stash changes, and push code to the central repo.
# CATEGORY: SYSTEM UTILITIES

import os
import subprocess

def main():
    print("\n[*] Launching Git Sync Manager...")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    script_path = os.path.join(base_dir, "git-menu.ps1")
    subprocess.run(["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", script_path], cwd=base_dir)

if __name__ == "__main__":
    main()
