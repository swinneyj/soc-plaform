#!/usr/bin/env python3
"""
Closure Notes Generator
Generates formatted closure notes for incidents based on rule templates.
"""

import os
import sys
import json
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

# Define templates per rule type
CLOSURE_TEMPLATES = {
    "suspicious_ad_recon": {
        "name": "Suspicious Active Directory Reconnaissance",
        "fields": ["source_ip", "dest_ip", "username", "tool_name", "justification"],
        "template": """
CLOSURE NOTES - Suspicious AD Reconnaissance

Source IP: {source_ip}
Destination IP: {dest_ip}
Username: {username}
Tool Detected: {tool_name}

Justification for Closure:
{justification}

Status: CLOSED - BENIGN
Analyst: Automated
Date: {{date}}
""",
    },
    "lateral_movement": {
        "name": "Lateral Movement Detection",
        "fields": ["source_ip", "dest_ip", "process", "method", "evidence", "action_taken"],
        "template": """
CLOSURE NOTES - Lateral Movement Alert

Source Host: {source_ip}
Target Host: {dest_ip}
Process/Tool: {process}
Method: {method}

Supporting Evidence:
{evidence}

Actions Taken:
{action_taken}

Status: CLOSED
Analyst: Automated
Date: {{date}}
""",
    },
    "malware_detection": {
        "name": "Malware Detection",
        "fields": ["host", "file_hash", "file_path", "detection_name", "action", "quarantine_status"],
        "template": """
CLOSURE NOTES - Malware Detection

Affected Host: {host}
File Hash (MD5): {file_hash}
File Path: {file_path}
Detection Name: {detection_name}

Action Taken: {action}
Quarantine Status: {quarantine_status}

Status: CLOSED - REMEDIATED
Analyst: Automated
Date: {{date}}
""",
    },
    "failed_authentication": {
        "name": "Failed Authentication Attempts",
        "fields": ["username", "source_ip", "target_system", "attempt_count", "resolution"],
        "template": """
CLOSURE NOTES - Failed Authentication

Username: {username}
Source IP: {source_ip}
Target System: {target_system}
Failed Attempts: {attempt_count}

Resolution:
{resolution}

Status: CLOSED
Analyst: Automated
Date: {{date}}
""",
    },
}

def list_templates() -> List[Dict]:
    """List available closure note templates."""
    templates = []
    for key, template in CLOSURE_TEMPLATES.items():
        templates.append({
            "id": key,
            "name": template["name"],
            "fields": template["fields"]
        })
    return templates

def generate_closure_note(rule_id: str, values: Dict[str, str]) -> Dict:
    """Generate a closure note from a template."""
    if rule_id not in CLOSURE_TEMPLATES:
        return {
            "success": False,
            "error": f"Unknown rule: {rule_id}. Available: {', '.join(CLOSURE_TEMPLATES.keys())}"
        }
    
    template_info = CLOSURE_TEMPLATES[rule_id]
    template_text = template_info["template"]
    
    # Validate required fields
    missing = []
    for field in template_info["fields"]:
        if field not in values or not values[field]:
            missing.append(field)
    
    if missing:
        return {
            "success": False,
            "error": f"Missing required fields: {', '.join(missing)}"
        }
    
    try:
        from datetime import datetime
        note = template_text.format(**values)
        note = note.replace("{date}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        return {
            "success": True,
            "rule": template_info["name"],
            "closure_note": note.strip()
        }
    except KeyError as e:
        return {
            "success": False,
            "error": f"Invalid field in template: {str(e)}"
        }

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate closure notes for incidents")
    parser.add_argument('--list', action='store_true', help='List available templates')
    parser.add_argument('--rule', type=str, help='Rule ID to generate note for')
    parser.add_argument('--values', type=str, help='JSON object with field values')
    parser.add_argument('--output', type=str, help='Output file to save closure note')
    
    args = parser.parse_args()
    
    if args.list:
        print(f"{Colors.CYAN}[*] Available Closure Note Templates:{Colors.ENDC}\n")
        for template in list_templates():
            print(f"{Colors.GREEN}{template['name']}{Colors.ENDC}")
            print(f"   ID: {template['id']}")
            print(f"   Fields: {', '.join(template['fields'])}\n")
        return
    
    if not args.rule or not args.values:
        print(f"{Colors.FAIL}[!] Usage: --rule <id> --values '{json.dumps({})}'{{Colors.ENDC}}")
        print(f"   Or use: --list to see templates")
        return
    
    try:
        values = json.loads(args.values)
    except json.JSONDecodeError:
        print(f"{Colors.FAIL}[!] Invalid JSON in --values{Colors.ENDC}")
        return
    
    result = generate_closure_note(args.rule, values)
    
    if result["success"]:
        print(result["closure_note"])
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(result["closure_note"])
            print(f"\n{Colors.GREEN}[+] Saved to: {args.output}{Colors.ENDC}")
    else:
        print(f"{Colors.FAIL}[!] Error: {result['error']}{Colors.ENDC}")

if __name__ == "__main__":
    main()
