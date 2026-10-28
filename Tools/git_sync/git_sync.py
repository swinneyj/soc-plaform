# TOOL_NAME: Git_Sync_Manager
# DESC: Interactive Git utility to safely pull updates, stash changes, and push code to the central repo.
# CATEGORY: SYSTEM UTILITIES

import os
import subprocess
import shutil

def main():
    print("\n[*] Launching Git Sync Manager...")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    script_path = os.path.join(base_dir, "git-menu.ps1")
    if os.name == "nt":
        shell = "powershell.exe"
    else:
        # Keep the catalog entry testable on macOS/Linux while retaining the
        # Windows PowerShell path used on the deployed workstation.
        shell = "pwsh" if shutil.which("pwsh") else None

    if not shell:
        print("[!] PowerShell is required for Git_Sync_Manager (install PowerShell 7 as 'pwsh').")
        return

    # shell=False list form; the script is a fixed in-repo file, never input.
    script_path = os.path.abspath(script_path)
    if os.path.commonpath([script_path, base_dir]) != base_dir:
        print("[!] Refusing: script path escapes the repository.")
        return
    if not (os.path.isfile(script_path) and script_path.lower().endswith(".ps1")):
        print("[!] Refusing: git-menu.ps1 is missing or has an unexpected extension.")
        return

    subprocess.run([shell, "-ExecutionPolicy", "Bypass", "-File", script_path], cwd=base_dir, check=False)

if __name__ == "__main__":
    main()
