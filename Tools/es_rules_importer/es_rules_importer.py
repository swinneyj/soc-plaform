#!/usr/bin/env python3
"""
Splunk ES Correlation Rules Importer
Imports Splunk Enterprise Security correlation rules into the database.
"""

import os
import sys
import json
from pathlib import Path

# Ensure Tools and the overall platform root are on sys.path
tools_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, tools_root)

# Platform root is the parent of Tools
platform_root = os.path.dirname(tools_root)
if platform_root not in sys.path:
    sys.path.insert(0, platform_root)

from core_lib.utils import get_platform_root


class Colors:
    """Fallback ANSI color codes for CLI output.
    Defined here to avoid import errors if not provided by core_lib.utils.
    """
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"

def import_rules_from_json(json_file: str, silent: bool = False) -> dict:
    """Import rules from JSON file into database."""
    if not os.path.exists(json_file):
        return {"success": False, "error": f"File not found: {json_file}", "imported": 0}
    
    try:
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, ESCorrelationRule, SupportiveQuery
        
        with open(json_file, 'r') as f:
            rules_data = json.load(f)
        
        # Handle both array and object with "rules" key
        if isinstance(rules_data, dict) and "rules" in rules_data:
            rules = rules_data["rules"]
        elif isinstance(rules_data, list):
            rules = rules_data
        else:
            return {"success": False, "error": "JSON must be array or object with 'rules' key", "imported": 0}
        
        db = SessionLocal()
        imported = 0
        updated = 0
        skipped = 0
        supportive_imported = 0
        supportive_updated = 0
        supportive_skipped = 0
        errors = []
        # Track the next explicit ID to assign for new supportive queries.
        # This avoids relying on a misaligned Postgres sequence and is safe
        # for small, admin-driven imports.
        try:
            max_id = db.query(func.max(SupportiveQuery.id)).scalar() or 0
        except Exception:
            max_id = 0
        next_supportive_id = int(max_id) + 1
        
        for rule_data in rules:
            try:
                # Check if rule already exists
                existing = db.query(ESCorrelationRule).filter(
                    ESCorrelationRule.rule_id == rule_data.get("rule_id")
                ).first()

                # If the rule already exists, reuse it so we can still attach supportive queries
                if existing:
                    rule = existing
                    rule.rule_name = rule_data.get("rule_name", rule.rule_name)
                    rule.description = rule_data.get("description", rule.description)
                    rule.category = rule_data.get("category", rule.category)
                    rule.severity = rule_data.get("severity", rule.severity)
                    if "drilldown_fields" in rule_data:
                        rule.drilldown_fields = json.dumps(rule_data.get("drilldown_fields", []))
                    if "required_closure_fields" in rule_data:
                        rule.required_closure_fields = json.dumps(rule_data.get("required_closure_fields", []))
                    if "closure_template" in rule_data:
                        rule.closure_template = rule_data.get("closure_template", rule.closure_template)
                    if "enabled" in rule_data:
                        rule.enabled = rule_data.get("enabled", rule.enabled)
                    updated += 1
                else:
                    # Create rule record
                    rule = ESCorrelationRule(
                        rule_id=rule_data.get("rule_id", ""),
                        rule_name=rule_data.get("rule_name", ""),
                        description=rule_data.get("description", ""),
                        category=rule_data.get("category", "Unknown"),
                        severity=rule_data.get("severity", "medium"),
                        drilldown_fields=json.dumps(rule_data.get("drilldown_fields", [])),
                        required_closure_fields=json.dumps(rule_data.get("required_closure_fields", [])),
                        closure_template=rule_data.get("closure_template", ""),
                        enabled=rule_data.get("enabled", 1)
                    )
                    db.add(rule)
                    imported += 1

                # Optional: import any supportive queries tied to this rule.
                #
                # IMPORTANT: once a rule has any supportive queries in the
                # database (whether seeded or edited via the UI), we treat the
                # DB as the source of truth and do not re-import JSON seeds for
                # that rule again. This prevents deleted/edited queries from
                # being resurrected on every sync.

                existing_for_rule = db.query(SupportiveQuery).filter(
                    SupportiveQuery.rule_id == rule.rule_id
                ).all()

                if existing_for_rule:
                    # Rule already has supportive queries in DB; skip JSON
                    # seeds for this rule to preserve analyst customizations.
                    supportive_skipped += len(rule_data.get("supportive_queries", []))
                else:
                    for q in rule_data.get("supportive_queries", []):
                        try:
                            title = q.get("title", "").strip()
                            spl_query = q.get("spl_query", "").strip()
                            if not title or not spl_query:
                                supportive_skipped += 1
                                continue

                            # Avoid duplicate supportive queries for the same rule & title
                            existing_q = db.query(SupportiveQuery).filter(
                                SupportiveQuery.rule_id == rule.rule_id,
                                SupportiveQuery.title == title
                            ).first()
                            if existing_q:
                                existing_q.description = q.get("description", existing_q.description)
                                existing_q.spl_query = spl_query
                                supportive_updated += 1
                                continue

                            sq = SupportiveQuery(
                                id=next_supportive_id,
                                rule_id=rule.rule_id,
                                title=title,
                                description=q.get("description", ""),
                                spl_query=spl_query
                            )
                            next_supportive_id += 1
                            db.add(sq)
                            supportive_imported += 1
                        except Exception as sq_e:
                            errors.append(f"Supportive query for rule '{rule.rule_id}': {str(sq_e)[:100]}")
                
            except Exception as e:
                skipped += 1
                errors.append(f"Rule '{rule_data.get('rule_name', 'Unknown')}': {str(e)[:100]}")
        
        db.commit()
        db.close()
        
        if not silent:
            print(f"{Colors.GREEN}[+] Import complete!{Colors.ENDC}")
            print(f"    Rules - Imported: {imported} | Updated: {updated} | Skipped: {skipped}")
            print(f"    Supportive Queries - Imported: {supportive_imported} | Updated: {supportive_updated} | Skipped: {supportive_skipped}")
            if errors:
                print(f"    Errors: {len(errors)}")
                for err in errors[:5]:
                    print(f"      - {err}")
        
        return {
            "success": True,
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "supportive_imported": supportive_imported,
            "supportive_updated": supportive_updated,
            "supportive_skipped": supportive_skipped,
            "errors": errors
        }
    
    except Exception as e:
        return {"success": False, "error": str(e), "imported": 0}

def list_rules(silent: bool = False) -> dict:
    """List all imported rules."""
    try:
        from db.models import SessionLocal, ESCorrelationRule
        
        db = SessionLocal()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()
        db.close()
        
        result = {
            "success": True,
            "total": len(rules),
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "rule_name": r.rule_name,
                    "category": r.category,
                    "severity": r.severity,
                    "description": r.description[:100] + "..." if len(r.description) > 100 else r.description
                }
                for r in rules
            ]
        }
        
        if not silent:
            print(f"{Colors.CYAN}[*] Available ES Correlation Rules:{Colors.ENDC}\n")
            for rule in result["rules"]:
                print(f"{Colors.GREEN}{rule['rule_name']}{Colors.ENDC}")
                print(f"   ID: {rule['rule_id']}")
                print(f"   Category: {rule['category']} | Severity: {rule['severity']}\n")
        
        return result
    
    except Exception as e:
        return {"success": False, "error": str(e), "total": 0, "rules": []}

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Import Splunk ES correlation rules")
    parser.add_argument('--import', type=str, dest='import_file', help='JSON file to import')
    parser.add_argument('--list', action='store_true', help='List all imported rules')
    parser.add_argument('--silent', action='store_true', help='Suppress output')
    
    args = parser.parse_args()
    
    if args.import_file:
        result = import_rules_from_json(args.import_file, args.silent)
        if not result["success"]:
            print(f"{Colors.FAIL}[!] Error: {result['error']}{Colors.ENDC}")
            sys.exit(1)
        sys.exit(0)
    
    if args.list or not args.import_file:
        list_rules(args.silent)

if __name__ == "__main__":
    main()
