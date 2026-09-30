import re

# Read current file (but remove the old exception handler first)
with open('api/main.py', 'r') as f:
    content = f.read()

# Find and remove the duplicate exception handler at the end
pattern = r'@app\.exception_handler\(Exception\)[\s\S]*?(?=if __name__)'
content = re.sub(pattern, '', content)

# Add new endpoints before the exception handler
new_endpoints = '''
# Rules & Closure Notes Endpoints
@app.get("/api/db/rules", tags=["Rules"])
def list_rules():
    """List all available ES correlation rules."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule
        db = SessionLocal()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()
        db.close()
        
        return [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "description": r.description,
                "category": r.category,
                "severity": r.severity,
                "drilldown_fields": json.loads(r.drilldown_fields) if r.drilldown_fields else [],
                "required_closure_fields": json.loads(r.required_closure_fields) if r.required_closure_fields else []
            }
            for r in rules
        ]
    except Exception as e:
        return []

@app.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate a closure note for a case based on rule template."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, ClosureNote, TriageResult
        
        rule_id = request.get('rule_id')
        case_id = request.get('case_id')
        field_values = request.get('field_values', {})
        analyst_notes = request.get('analyst_notes', '')
        
        if not rule_id or not case_id:
            raise HTTPException(status_code=400, detail="rule_id and case_id required")
        
        db = SessionLocal()
        rule = db.query(ESCorrelationRule).filter(ESCorrelationRule.rule_id == rule_id).first()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        
        if not rule:
            db.close()
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
        
        # Build closure note from template
        template = rule.closure_template
        try:
            generated_note = template.format(**field_values)
            from datetime import datetime as dt
            generated_note = generated_note.replace("{date}", dt.now().strftime("%Y-%m-%d %H:%M:%S"))
            generated_note = generated_note.replace("{rule_name}", rule.rule_name)
            generated_note = generated_note.replace("{case_id}", case_id)
        except KeyError as e:
            db.close()
            raise HTTPException(status_code=400, detail=f"Missing field in template: {str(e)}")
        
        # Save closure note
        closure_note = ClosureNote(
            case_id=case_id,
            rule_id=rule_id,
            analyst_notes=analyst_notes,
            generated_note=generated_note,
            status="draft"
        )
        db.add(closure_note)
        db.commit()
        db.close()
        
        return {
            "success": True,
            "case_id": case_id,
            "rule_name": rule.rule_name,
            "generated_note": generated_note
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )

'''

# Add before "# Serve web UI"
content = content.replace("# Serve web UI", new_endpoints + "# Serve web UI")

with open('api/main.py', 'w') as f:
    f.write(content)

print("Updated api/main.py with rules endpoints")
