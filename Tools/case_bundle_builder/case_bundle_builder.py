# TOOL_NAME: Case_Bundle_Builder
# DESC: Builds a consolidated incident case bundle from a folder of exports.
# CATEGORY: Reporting
# SOURCE_TYPES: offline_exports
# ARG: --source_dir | Source Directory | Folder containing raw exports for this case | True

import os
import sys
import shutil
import json
import datetime
import subprocess

# Import shared core library
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from core_lib.utils import Colors, clear_screen, get_platform_root


def discover_files(source_dir):
    """Return a list of files under source_dir (non-recursive for v1)."""
    files = []
    try:
        for name in os.listdir(source_dir):
            path = os.path.join(source_dir, name)
            if os.path.isfile(path):
                files.append(path)
    except Exception:
        pass
    return files


def classify_file(path):
    """Very lightweight type tagging based on filename/extension.

    This remains intentionally simple; it just helps routing without
    changing any live system behavior.
    """
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]

    if 'mde' in name or 'defender' in name or 'incident' in name or 'alerts' in name:
        return 'mde_export'
    if 'splunk' in name or 'notable' in name:
        return 'splunk_export'
    if ext in ['.csv', '.txt', '.log']:
        return 'generic_text_export'
    if ext in ['.json']:
        return 'json_export'
    return 'unknown'


def run_processor(tool_path, args_list, derived_dir, label, derived_outputs):
    """Run an existing SOC tool as a subprocess and attempt to locate its output.

    The current MDE/Splunk/IOC tools write into Data/Reports or Data/Archive.
    To keep the case bundle self-contained, we move any newly created files
    into the case's derived/ folder when possible.
    """
    try:
        platform_root = get_platform_root()
        # Snapshot existing report/archive files before running the tool
        reports_dir = os.path.join(platform_root, 'Data', 'Reports')
        archive_dir = os.path.join(platform_root, 'Data', 'Archive')
        existing = set()
        for base in [reports_dir, archive_dir]:
            if os.path.isdir(base):
                for name in os.listdir(base):
                    existing.add(os.path.join(base, name))

        # Execute the tool (silent flags are passed where supported)
        parsed_args = list(args_list)
        subprocess.run([sys.executable, tool_path] + parsed_args, input=b"\n\n\n")

        # Detect new files after execution
        new_files = []
        for base in [reports_dir, archive_dir]:
            if os.path.isdir(base):
                for name in os.listdir(base):
                    full = os.path.join(base, name)
                    if full not in existing:
                        new_files.append(full)

        for src in new_files:
            dest = os.path.join(derived_dir, os.path.basename(src))
            try:
                shutil.copy2(src, dest)
                derived_outputs.append({
                    'processor': label,
                    'source_path': src,
                    'case_path': dest,
                })
            except Exception as e:
                derived_outputs.append({
                    'processor': label,
                    'source_path': src,
                    'case_path': None,
                    'error': str(e),
                })
    except Exception as e:
        derived_outputs.append({
            'processor': label,
            'error': f"Failed to run processor: {e}",
        })


def build_case_bundle(source_dir):
    platform_root = get_platform_root()
    exports_root = os.path.join(platform_root, 'Data', 'Exports')
    os.makedirs(exports_root, exist_ok=True)

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    case_id = f"Case_{timestamp}"
    case_dir = os.path.join(exports_root, case_id)
    raw_dir = os.path.join(case_dir, 'raw')
    derived_dir = os.path.join(case_dir, 'derived')
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(derived_dir, exist_ok=True)

    files = discover_files(source_dir)
    manifest_entries = []
    derived_outputs = []

    # Copy source files into the case bundle and tag them
    for src_path in files:
        try:
            file_type = classify_file(src_path)
            dest_name = os.path.basename(src_path)
            dest_path = os.path.join(raw_dir, dest_name)
            shutil.copy2(src_path, dest_path)

            manifest_entries.append({
                'file_name': dest_name,
                'original_path': src_path,
                'type': file_type,
            })
        except Exception as e:
            manifest_entries.append({
                'file_name': os.path.basename(src_path),
                'original_path': src_path,
                'type': 'error_copying',
                'error': str(e),
            })

    # Auto-run processors for recognized file types (MDE, Splunk, IOC)
    tools_root = os.path.join(platform_root, 'Tools')
    mde_tool = os.path.join(tools_root, 'mde_triage_aggregator', 'mde_triage_aggregator.py')
    splunk_tool = os.path.join(tools_root, 'splunk_notable_parser', 'splunk_notable_parser.py')
    ioc_tool = os.path.join(tools_root, 'ioc_extractor', 'ioc_extractor.py')

    # MDE exports
    if os.path.isfile(mde_tool):
        for entry in manifest_entries:
            if entry.get('type') == 'mde_export':
                src_case_path = os.path.join(raw_dir, entry['file_name'])
                run_processor(
                    mde_tool,
                    ['--file', src_case_path],
                    derived_dir,
                    'mde_triage_aggregator',
                    derived_outputs,
                )

    # Splunk exports
    if os.path.isfile(splunk_tool):
        for entry in manifest_entries:
            if entry.get('type') == 'splunk_export':
                src_case_path = os.path.join(raw_dir, entry['file_name'])
                run_processor(
                    splunk_tool,
                    ['--file', src_case_path, '--silent'],
                    derived_dir,
                    'splunk_notable_parser',
                    derived_outputs,
                )

    # IOC extraction across the entire case raw folder
    if os.path.isfile(ioc_tool):
        run_processor(
            ioc_tool,
            ['--target', raw_dir, '--silent'],
            derived_dir,
            'ioc_extractor',
            derived_outputs,
        )

    manifest = {
        'case_id': case_id,
        'created_utc': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source_dir': source_dir,
        'files': manifest_entries,
        'derived_outputs': derived_outputs,
        'notes': 'Case bundle with source files and auto-derived MDE/Splunk/IOC summaries when applicable.'
    }

    manifest_path = os.path.join(case_dir, 'case_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    return case_dir, manifest_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Incident Case Bundle Builder')
    parser.add_argument('--source_dir', type=str, help='Folder containing exports for this case')
    args = parser.parse_args()

    clear_screen()
    print(f"{Colors.CYAN}{Colors.BOLD}" + "="*80)
    print(" INCIDENT CASE BUNDLE BUILDER ".center(80))
    print("="*80 + f"{Colors.ENDC}")

    source_dir = args.source_dir
    if not source_dir:
        print(f"\n{Colors.HEADER}Provide the folder that contains all exports for this case.{Colors.ENDC}")
        print(f"{Colors.CYAN}TIP: Drag and drop the folder here if your shell supports it.{Colors.ENDC}")
        source_dir = input(f"\n{Colors.BLUE}Source Folder>{Colors.ENDC} ").strip()

    source_dir = source_dir.strip('"').strip("'") if source_dir else ''

    if not source_dir or not os.path.isdir(source_dir):
        print(f"\n{Colors.FAIL}[!] Source directory not found or invalid: {source_dir}{Colors.ENDC}")
        if not os.environ.get('COMMANDER_BOOT'):
            input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")
        return

    print(f"\n{Colors.CYAN}[*] Building case bundle from: {source_dir}{Colors.ENDC}")
    case_dir, manifest_path = build_case_bundle(source_dir)

    print(f"\n{Colors.GREEN}[+] Case bundle created!{Colors.ENDC}")
    print(f"{Colors.GREEN}[+] Case folder:{Colors.ENDC} {case_dir}")
    print(f"{Colors.GREEN}[+] Manifest:{Colors.ENDC} {manifest_path}")
    print(f"\n{Colors.CYAN}[i] Next steps: Use AI_Analyst_Engine or Automated_Reporter on files inside this case folder to generate analysis prompts and briefings.{Colors.ENDC}")

    if not os.environ.get('COMMANDER_BOOT'):
        input(f"\n{Colors.BLUE}Press Enter to return to Commander...{Colors.ENDC}")


if __name__ == '__main__':
    main()
