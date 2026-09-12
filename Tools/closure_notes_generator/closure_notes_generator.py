#!/usr/bin/env python3
# TOOL_NAME: closure_notes_generator
# DESC: Generates structured incident closure notes from rule templates and evidence values.
# CATEGORY: Uncategorized
"""
Closure Notes Generator
Generates formatted closure notes for incidents based on rule templates and supportive_rules.json.
"""

import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_lib.utils import get_platform_root, Colors

def load_supportive_rules() -> Dict[str, Dict]:
    """Load rule definitions and detection science from supportive_rules.json."""
    possible_paths = [
        os.path.join(get_platform_root(), "supportive_rules.json"),
        os.path.join(get_platform_root(), "config", "supportive_rules.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "supportive_rules.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "supportive_rules.json"),
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    rules_list = data.get("rules", data) if isinstance(data, dict) else data
                    if isinstance(rules_list, list):
                        return {r.get("rule_id"): r for r in rules_list if "rule_id" in r}
                    elif isinstance(rules_list, dict):
                        return rules_list
            except Exception as e:
                print(f"{Colors.WARNING}[!] Warning: Failed to load supportive_rules.json from {path}: {e}{Colors.ENDC}", file=sys.stderr)
    return {}

# Hardcoded base templates per rule type (can be supplemented dynamically)
CLOSURE_TEMPLATES = {
    "lotl_outbound_connection": {
        "name": "Living off the Land (LotL) Outbound Connection",
        "fields": ["host", "destination", "process", "evidence", "justification"],
        "template": """
CLOSURE NOTES - Living off the Land (LotL) Outbound Connection
{rationale_header}
Target Host: {host}
Suspicious Binary / Process: {process}
External Destination: {destination}

Supporting Evidence & Telemetry:
{evidence}

Closure Justification / Analyst Assessment:
{justification}

Status: CLOSED
Analyst: Automated
Date: {date}
""",
    },
    "suspicious_ad_recon": {
        "name": "Suspicious Active Directory Reconnaissance",
        "fields": ["source_ip", "dest_ip", "username", "tool_name", "justification"],
        "template": """
CLOSURE NOTES - Suspicious AD Reconnaissance
{rationale_header}
Source IP: {source_ip}
Destination IP: {dest_ip}
Username: {username}
Tool Detected: {tool_name}

Justification for Closure:
{justification}

Status: CLOSED - BENIGN
Analyst: Automated
Date: {date}
""",
    },
    "lateral_movement": {
        "name": "Lateral Movement Detection",
        "fields": ["source_ip", "dest_ip", "process", "method", "evidence", "action_taken"],
        "template": """
CLOSURE NOTES - Lateral Movement Alert
{rationale_header}
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
Date: {date}
""",
    },
    "malware_detection": {
        "name": "Malware Detection",
        "fields": ["host", "file_hash", "file_path", "detection_name", "action", "quarantine_status"],
        "template": """
CLOSURE NOTES - Malware Detection
{rationale_header}
Affected Host: {host}
File Hash (MD5): {file_hash}
File Path: {file_path}
Detection Name: {detection_name}

Action Taken: {action}
Quarantine Status: {quarantine_status}

Status: CLOSED - REMEDIATED
Analyst: Automated
Date: {date}
""",
    },
    "failed_authentication": {
        "name": "Failed Authentication Attempts",
        "fields": ["username", "source_ip", "target_system", "attempt_count", "resolution"],
        "template": """
CLOSURE NOTES - Failed Authentication
{rationale_header}
Username: {username}
Source IP: {source_ip}
Target System: {target_system}
Failed Attempts: {attempt_count}

Resolution:
{resolution}

Status: CLOSED
Analyst: Automated
Date: {date}
""",
    },
}

def build_rationale_header(rule_meta: Dict) -> str:
    """Constructs formatted detection rationale and MITRE header block."""
    if not rule_meta:
        return ""
    
    header_parts = []
    
    mitre = rule_meta.get("mitre_techniques")
    if mitre:
        mitre_str = ", ".join(mitre) if isinstance(mitre, list) else str(mitre)
        header_parts.append(f"MITRE ATT&CK: {mitre_str}")
        
    trigger = rule_meta.get("adaptive_response_trigger")
    if trigger:
        header_parts.append(f"Trigger: {trigger}")
        
    rationale = rule_meta.get("detection_rationale")
    if rationale:
        header_parts.append(f"\nDetection Rationale:\n{rationale}")
        
    if header_parts:
        return "\n" + "\n".join(header_parts) + "\n"
    return ""

def list_templates() -> List[Dict]:
    """List available closure note templates and supportive rules."""
    supportive_rules = load_supportive_rules()
    templates = []
    
    # Combined keys from hardcoded templates and supportive_rules.json
    all_keys = set(CLOSURE_TEMPLATES.keys()).union(supportive_rules.keys())
    
    for key in sorted(all_keys):
        if key in CLOSURE_TEMPLATES:
            tpl = CLOSURE_TEMPLATES[key]
            name = tpl["name"]
            fields = tpl["fields"]
        else:
            rule_info = supportive_rules[key]
            name = rule_info.get("rule_name", key)
            fields = ["evidence", "justification", "status"]
            
        templates.append({
            "id": key,
            "name": name,
            "fields": fields
        })
    return templates

def generate_closure_note(rule_id: str, values: Dict[str, str]) -> Dict:
    """Generate a closure note from a template combined with supportive rule science."""
    supportive_rules = load_supportive_rules()
    rule_meta = supportive_rules.get(rule_id, {})
    
    rationale_header = build_rationale_header(rule_meta)
    
    if rule_id in CLOSURE_TEMPLATES:
        template_info = CLOSURE_TEMPLATES[rule_id]
        template_text = template_info["template"]
        required_fields = template_info["fields"]
        rule_display_name = template_info["name"]
    elif rule_meta:
        # Dynamic fallback template when rule exists only in supportive_rules.json
        rule_display_name = rule_meta.get("rule_name", rule_id)
        required_fields = ["evidence", "justification"]
        template_text = """
CLOSURE NOTES - {rule_name}
{rationale_header}
Supporting Evidence & Telemetry:
{evidence}

Analyst Justification:
{justification}

Status: {status}
Analyst: Automated
Date: {date}
"""
    else:
        return {
            "success": False,
            "error": f"Unknown rule: {rule_id}. Available: {', '.join(sorted(set(CLOSURE_TEMPLATES.keys()).union(supportive_rules.keys())))}"
        }
    
    # Validate required fields
    missing = [f for f in required_fields if f not in values or not values[f]]
    if missing:
        return {
            "success": False,
            "error": f"Missing required fields: {', '.join(missing)}"
        }
    
    try:
        format_args = {
            **values,
            "rule_name": rule_display_name,
            "rationale_header": rationale_header,
            "status": values.get("status", "CLOSED"),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        note = template_text.format(**format_args)
        
        return {
            "success": True,
            "rule": rule_display_name,
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
        print(f"{Colors.FAIL}[!] Usage: --rule <id> --values '{json.dumps({})}'{Colors.ENDC}")
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
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(result["closure_note"])
            print(f"\n{Colors.GREEN}[+] Saved to: {args.output}{Colors.ENDC}")
    else:
        print(f"{Colors.FAIL}[!] Error: {result['error']}{Colors.ENDC}")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# TOOL_NAME: closure_notes_generator
# DESC: Generates structured incident closure notes from rule templates and evidence values.
# CATEGORY: Uncategorized
