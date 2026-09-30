import re

with open('api/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the closure-note endpoint
old_pattern = r'@app\.post\("/api/db/closure-note".*?(?=@app\.exception_handler|$)'
old_endpoint = '''@app.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate a closure note for a case based on rule template."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, ClosureNote, TriageResult
        
        rule_id = request.get('rule_id')
        case_id = request.get('case_id')
        field_values = request.get('field_values', {})
        analyst_notes = request.get('analyst_notes', '')
        disposition = request.get('disposition', 'Undetermined')
        
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
        
        # Build closure note from template - add all replacement values
        all_values = dict(field_values)
        all_values['rule_name'] = rule.rule_name
        all_values['case_id'] = case_id
        all_values['date'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        all_values['disposition'] = disposition
        
        template = rule.closure_template
        try:
            generated_note = template.format(**all_values)
        except KeyError as e:
            db.close()
            raise HTTPException(status_code=400, detail=f"Missing field in template: {str(e)}")
        
        # Map disposition to status
        status_map = {
            'True Positive': 'true_positive',
            'Benign Positive': 'benign_positive',
            'False Positive': 'false_positive',
            'Other': 'other',
            'Undetermined': 'undetermined'
        }
        closure_status = status_map.get(disposition, 'undetermined')
        
        # Save closure note
        closure_note = ClosureNote(
            case_id=case_id,
            rule_id=rule_id,
            analyst_notes=analyst_notes,
            generated_note=generated_note,
            status=closure_status
        )
        db.add(closure_note)
        db.commit()
        db.close()
        
        return {
            "success": True,
            "case_id": case_id,
            "rule_name": rule.rule_name,
            "disposition": disposition,
            "generated_note": generated_note
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))'''

new_endpoint = '''@app.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate a closure note for a case based on rule template."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, ClosureNote, TriageResult
        
        rule_id = request.get('rule_id')
        case_id = request.get('case_id')
        field_values = request.get('field_values', {})
        analyst_notes = request.get('analyst_notes', '')
        disposition = request.get('disposition', 'Undetermined')
        
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
        
        # Extract all data while session is active
        rule_name = rule.rule_name
        rule_template = rule.closure_template
        
        # Build closure note from template - add all replacement values
        all_values = dict(field_values)
        all_values['rule_name'] = rule_name
        all_values['case_id'] = case_id
        all_values['date'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        all_values['disposition'] = disposition
        
        try:
            generated_note = rule_template.format(**all_values)
        except KeyError as e:
            db.close()
            raise HTTPException(status_code=400, detail=f"Missing field in template: {str(e)}")
        
        # Map disposition to status
        status_map = {
            'True Positive': 'true_positive',
            'Benign Positive': 'benign_positive',
            'False Positive': 'false_positive',
            'Other': 'other',
            'Undetermined': 'undetermined'
        }
        closure_status = status_map.get(disposition, 'undetermined')
        
        # Save closure note
        closure_note = ClosureNote(
            case_id=case_id,
            rule_id=rule_id,
            analyst_notes=analyst_notes,
            generated_note=generated_note,
            status=closure_status
        )
        db.add(closure_note)
        db.commit()
        db.close()
        
        return {
            "success": True,
            "case_id": case_id,
            "rule_name": rule_name,
            "disposition": disposition,
            "generated_note": generated_note
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))'''

content = content.replace(old_endpoint, new_endpoint)

with open('api/main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed closure-note endpoint - now extracts data before session closes")
