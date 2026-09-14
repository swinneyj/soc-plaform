# TOOL_NAME: filecheck
# DESC: Reliably counts lines, searches for phrases, and hashes a file (Interactive).
# CATEGORY: Uncategorized

import hashlib
import os

def reliable_file_check():
    print("\n=== NATIVE FILE INTEGRITY CHECKER ===")
    filepath = input("Enter the full path to the file: ").strip().strip('"').strip("'")
    
    # STEP 1: Check if the file actually exists
    if not os.path.exists(filepath):
        print(f"\n[!] Error: The file {filepath} could not be found.")
        input("\nPress Enter to return to Commander...")
        return

    search_phrase = input("Enter the phrase to search for (or press Enter to skip search): ").strip()
    
    line_count = 0
    phrase_found = False
    
    print("\n[*] Analyzing file...")
    
    # STEP 2: Read the text line-by-line
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as text_file:
            for line in text_file:
                line_count += 1
                
                if search_phrase and not phrase_found and search_phrase in line:
                    phrase_found = True
                    
    except PermissionError:
        print("\n[!] Error: You do not have permission to read this file. It may be locked.")
        input("\nPress Enter to return to Commander...")
        return

    # STEP 3: Create the digital fingerprint (SHA-256 Hash)
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as binary_file:
        for byte_block in iter(lambda: binary_file.read(4096), b""):
            sha256_hash.update(byte_block)
            
    # STEP 4: Print the final report
    print("\n=== FILE INTEGRITY REPORT ===")
    print(f"Target File: {filepath}")
    print(f"Total Lines: {line_count:,}")
    if search_phrase:
        print(f"Did we find '{search_phrase}'? : {phrase_found}")
    print(f"SHA-256 Fingerprint: {sha256_hash.hexdigest()}")
    print("=============================\n")
    
    input("Press Enter to return to Commander...")

if __name__ == "__main__":
    reliable_file_check()
