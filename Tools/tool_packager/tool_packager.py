# TOOL_NAME: Tool_Packager
# DESC: Compresses the SOC platform into a ZIP, injecting a resilient launcher and dynamically generated README.
# CATEGORY: System Utilities

import os
import zipfile
import datetime
from collections import defaultdict

def build_dynamic_readme(platform_root):
    """Scans the platform for tools and builds a comprehensive README text."""
    readme_content = """==================================================
          SOC ORCHESTRATION PLATFORM (RELEASE)
==================================================

QUICK START:
1. Ensure Python 3 is installed on your system. For the web UI at
    http://localhost:8000, Docker Desktop must also be running.
2. Preferred: double-click 'launch_commander.bat'. This will call
    'Launch_Soc_Platform.bat' (if present) to start both:
      - The SOC Platform web UI (Docker FastAPI service on localhost:8000)
      - The SOC Commander CLI window.
3. Fallback: if Docker is not available, 'launch_commander.bat' will
    start only the SOC Commander CLI (no web UI).

COMMANDS:
* reload       - Scans the folder for new tools and updates the menu.
* info [Name]  - View details about a specific tool.
* edit [Name]  - Opens the tool's source code in Notepad.
* exit         - Closes the orchestrator safely.

==================================================
        INSTALLED TOOLS DIRECTORY
==================================================
"""
    tools_by_cat = defaultdict(list)
    ignore_dirs = {'.git', '__pycache__', '.idea', 'venv', 'env', 'SOC_Archive'}

    for root, dirs, files in os.walk(platform_root):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
        for file in files:
            if file.endswith(('.py', '.ps1')):
                file_path = os.path.join(root, file)
                
                tool_name = file
                desc = "No description provided."
                cat = "Uncategorized"

                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for _ in range(15):
                            line = f.readline().strip()
                            if line.startswith('# TOOL_NAME:'): tool_name = line.split(':', 1)[1].strip()
                            elif line.startswith('# DESC:'): desc = line.split(':', 1)[1].strip()
                            elif line.startswith('# CATEGORY:'): cat = line.split(':', 1)[1].strip()
                except Exception:
                    pass
                
                tools_by_cat[cat].append((tool_name, desc))

    for cat in sorted(tools_by_cat.keys()):
        readme_content += f"\n--- {cat.upper()} ---\n"
        for name, desc in sorted(tools_by_cat[cat]):
            readme_content += f"* {name}\n  -> {desc}\n"

    return readme_content

def package_platform():
    print("\n" + "="*50)
    print(" SOC PLATFORM PACKAGER ")
    print("="*50)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    platform_root = os.path.abspath(os.path.join(base_dir, '..', '..'))
    
    if not os.path.exists(os.path.join(platform_root, 'commander.py')):
        platform_root = base_dir

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    output_filename = f"SOC_Platform_Release_{timestamp}.zip"
    output_path = os.path.join(platform_root, output_filename)

    ignore_dirs = {'.git', '__pycache__', '.idea', 'venv', 'env', 'SOC_Archive'}
    ignore_exts = {'.zip', '.bak', '.old'}

    # Bulletproof Batch Content
    bat_content = """@echo off
title SOC Platform Launcher
color 0A
cd /d "%~dp0"

rem Prefer the unified SOC Platform launcher if available
if exist Launch_Soc_Platform.bat (
    echo Starting SOC Platform (GUI + CLI)...
    call Launch_Soc_Platform.bat
) else (
    echo Launch_Soc_Platform.bat not found. Starting SOC Commander CLI only...
    python commander.py
    pause
)
"""
    print("[*] Compiling dynamic tool index for README...")
    readme_content = build_dynamic_readme(platform_root)

    print(f"[*] Target Directory: {platform_root}")
    print("[*] Compressing files into release package...")

    try:
        # Some Windows/source-control restores carry timestamps before the ZIP
        # format epoch.  Let Python clamp those timestamps instead of failing
        # the entire package build.
        with zipfile.ZipFile(
            output_path,
            'w',
            zipfile.ZIP_DEFLATED,
            strict_timestamps=False,
        ) as zipf:
            zipf.writestr('launch_commander.bat', bat_content)
            zipf.writestr('README.txt', readme_content)
            print("  [+] Injected resilient launch_commander.bat")
            print("  [+] Injected README.txt (with dynamic tool index)")

            for root, dirs, files in os.walk(platform_root):
                dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]

                for file in files:
                    if any(file.endswith(ext) for ext in ignore_exts) or file == output_filename:
                        continue
                    
                    if file in ['launch_commander.bat', 'README.txt'] and root == platform_root:
                        continue
                        
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, start=platform_root)
                    zipf.write(file_path, arcname)
                    
        print(f"\n[+] Packaging Complete!")
        print(f"[+] Output saved to: {output_path}")

    except Exception as e:
        print(f"\n[!] Error during packaging: {e}")

    if not os.environ.get("COMMANDER_BOOT"):
        input("\nPress Enter to return to Commander...")

if __name__ == "__main__":
    package_platform()
