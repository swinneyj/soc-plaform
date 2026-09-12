#!/usr/bin/env python3
"""Run safe, offline regression checks for the Commander tool catalog."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, env=None, timeout=20):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    return result.returncode, (result.stdout + result.stderr).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    registry = json.loads((ROOT / 'Commander_Registry.json').read_text(encoding='utf-8'))
    results = []

    for tool in registry:
        path = ROOT / tool.get('path', '')
        ok = bool(tool.get('name') and tool.get('description') and tool.get('category') and path.is_file())
        results.append({'tool': tool.get('name', '(unnamed)'), 'check': 'registry_metadata',
                        'status': 'passed' if ok else 'failed', 'detail': str(path)})

    py_files = [ROOT / t['path'] for t in registry if str(t.get('path', '')).endswith('.py')]
    code, output = run([sys.executable, '-m', 'py_compile', *map(str, py_files)])
    results.append({'tool': 'catalog', 'check': 'python_syntax', 'status': 'passed' if code == 0 else 'failed',
                    'detail': output[-1000:]})

    with tempfile.TemporaryDirectory(prefix='soc-tool-regression-') as tmp:
        fixture = Path(tmp) / 'sample.log'
        fixture.write_text('Alert from 198.51.100.10 ticket-123 user test@example.com\npowershell.exe -enc SGVsbG8=\n', encoding='utf-8')
        specs = [
            ('base64_decoder', ['--target', 'SGVsbG8=', '--silent']),
            ('Data_Ingestor_Pipeline', ['--target', str(fixture), '--mode', '3', '--silent']),
            ('IOC_Extractor', ['--target', str(fixture), '--silent']),
            ('text_reformatter', ['--target', str(fixture), '--width', '60', '--silent']),
            ('Text_Sanitizer_Pipeline', ['--target', str(fixture), '--mode', '2', '--width', '60', '--silent']),
        ]
        by_name = {t['name']: t for t in registry}
        for name, extra in specs:
            tool = by_name.get(name)
            if not tool:
                results.append({'tool': name, 'check': 'offline_fixture', 'status': 'failed', 'detail': 'not registered'})
                continue
            code, output = run([sys.executable, str(ROOT / tool['path']), *extra],
                               env={**os.environ, 'COMMANDER_BOOT': '1'})
            results.append({'tool': name, 'check': 'offline_fixture', 'status': 'passed' if code == 0 else 'failed',
                            'detail': output[-1000:]})

    gated = {
        'cve_intel_fetcher': 'live network', 'Threat_Intel_Fetcher': 'live network',
        'bulk_rule_matcher': 'database service', 'es_rules_importer': 'database service',
        'splunk_csv_ingestor': 'database service', 'splunk_folder_watcher': 'database and file movement',
        'Workspace_Cleaner': 'destructive file movement', 'Git_Sync_Manager': 'external Git/PowerShell session',
    }
    for name, reason in gated.items():
        results.append({'tool': name, 'check': 'gated', 'status': 'requires_service', 'detail': reason})

    counts = {}
    for row in results:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    payload = {'suite': 'tool_catalog_regression', 'counts': counts, 'results': results}
    print(json.dumps(payload, indent=2) if args.json else f'Tool catalog regression: {counts}')
    return 1 if counts.get('failed', 0) else 0


if __name__ == '__main__':
    raise SystemExit(main())
