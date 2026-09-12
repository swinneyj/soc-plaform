#!/usr/bin/env python3
# TOOL_NAME: bulk_rule_matcher
# DESC: Matches triage cases to enabled ES correlation rules by exact or fuzzy rule name.
# CATEGORY: Uncategorized
"""
Bulk Rule Matcher
Matches triage cases to ES correlation rules by rule_name.
Updates triage_results with rule_id FK.
"""

import os
import sys
from difflib import SequenceMatcher

tools_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, tools_root)

# The catalog launches tools as standalone scripts.  In that mode Python's
# import path starts at Tools/bulk_rule_matcher rather than the platform root,
# so the repository-level ``db`` package is otherwise not importable on either
# Windows or POSIX hosts.
platform_root = os.path.dirname(tools_root)
if platform_root not in sys.path:
    sys.path.insert(0, platform_root)

from core_lib.utils import get_platform_root, Colors

def fuzzy_match(a: str, b: str, threshold: float = 0.8) -> bool:
    """Check if two strings are similar enough."""
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio >= threshold

def match_cases_to_rules(silent: bool = False) -> dict:
    """Match all triage cases to rules by name similarity."""
    try:
        from db.models import SessionLocal, TriageResult, ESCorrelationRule
        
        db = SessionLocal()
        cases = db.query(TriageResult).all()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()
        
        stats = {
            "total_cases": len(cases),
            "matched": 0,
            "unmatched": 0,
            "matches": []
        }
        
        for case in cases:
            best_match = None
            best_score = 0
            
            # Try exact match first
            exact = next((r for r in rules if r.rule_name.lower() == case.rule_name.lower()), None)
            if exact:
                best_match = exact
                best_score = 1.0
            else:
                # Try fuzzy match
                for rule in rules:
                    if fuzzy_match(case.rule_name, rule.rule_name, threshold=0.75):
                        ratio = SequenceMatcher(None, case.rule_name.lower(), rule.rule_name.lower()).ratio()
                        if ratio > best_score:
                            best_match = rule
                            best_score = ratio
            
            if best_match:
                case.rule_id = best_match.rule_id
                stats["matched"] += 1
                stats["matches"].append({
                    "case_id": case.case_id,
                    "case_rule": case.rule_name,
                    "matched_rule": best_match.rule_name,
                    "rule_id": best_match.rule_id,
                    "confidence": round(best_score * 100, 1)
                })
            else:
                stats["unmatched"] += 1
                if not silent:
                    print(f"{Colors.WARNING}[-] No match for: {case.rule_name}{Colors.ENDC}")
        
        db.commit()
        db.close()
        
        if not silent:
            print(f"{Colors.GREEN}[+] Bulk matching complete!{Colors.ENDC}")
            print(f"    Matched: {stats['matched']} / {stats['total_cases']}")
            print(f"    Unmatched: {stats['unmatched']}")
            if stats['matches']:
                print(f"\n{Colors.CYAN}Sample matches:{Colors.ENDC}")
                for m in stats['matches'][:5]:
                    print(f"  {m['case_id']}: {m['case_rule']} → {m['matched_rule']} ({m['confidence']}%)")
        
        return stats
    
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Match triage cases to ES correlation rules")
    parser.add_argument('--silent', action='store_true', help='Suppress output')
    
    args = parser.parse_args()
    
    result = match_cases_to_rules(args.silent)
    
    if "error" in result:
        print(f"{Colors.FAIL}[!] Error: {result['error']}{Colors.ENDC}")
        sys.exit(1)

if __name__ == "__main__":
    main()
