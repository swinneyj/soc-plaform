# TOOL_NAME: Workspace_Cleaner
# DESC: Organizes legacy tools into archives, consolidates paperwork, and safely removes empty directories.
# CATEGORY: System Utilities

import os
import shutil
import argparse

def consolidate_files(target_dir, archive_dir, doc_dir):
    """Organizes legacy SOC tools and consolidates paperwork."""
    print(f"[*] Organizing files in {target_dir}...")
    
    os.makedirs(archive_dir, exist_ok=True)
    os.makedirs(doc_dir, exist_ok=True)

    for root, _, files in os.walk(target_dir):
        # Skip the destination directories to avoid recursive loops
        if root.startswith(archive_dir) or root.startswith(doc_dir):
            continue

        for file in files:
            file_path = os.path.join(root, file)
            
            # Identify legacy tools or backups to archive
            if file.endswith(('.bak', '.old', '.deprecated')):
                shutil.move(file_path, os.path.join(archive_dir, file))
                print(f"  [+] Archived legacy tool: {file}")
                
            # Consolidate paperwork and operational documents
            elif file.endswith(('.pdf', '.docx', '.csv', '.txt')):
                shutil.move(file_path, os.path.join(doc_dir, file))
                print(f"  [+] Consolidated document: {file}")

def sweep_empty_folders(target_dir):
    """Recursively removes empty directories from the bottom up."""
    print(f"[*] Sweeping empty folders in {target_dir}...")
    # topdown=False ensures we process subdirectories before their parent directories
    for dirpath, _, _ in os.walk(target_dir, topdown=False):
        # Check if the directory is empty
        if not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
                print(f"  [-] Removed empty folder: {dirpath}")
            except OSError as e:
                # Catch errors in case a folder is locked by another process
                print(f"  [!] Could not remove {dirpath}: {e}")

def main():
    parser = argparse.ArgumentParser(description="SOC Workspace Cleaner")
    parser.add_argument("--target", default="./Downloads", help="Target directory to clean")
    args = parser.parse_args()

    # ==========================================
    # CRITICAL FIX: Strip literal quotes from the path
    # This prevents OSError: [WinError 123] when executed via Playbook_Runner
    # ==========================================
    clean_target = args.target.strip("\"'")

    # Define standard SOC folder structures using the cleaned path
    archive_path = os.path.join(clean_target, "SOC_Archive")
    docs_path = os.path.join(clean_target, "Consolidated_Paperwork")

    print("=== Initiating Workspace Cleanup ===")
    
    consolidate_files(clean_target, archive_path, docs_path)
    sweep_empty_folders(clean_target)
    
    print("=== Cleanup Complete ===")

if __name__ == "__main__":
    main()
