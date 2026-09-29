"""Code-review surface: sectioned review, whole-file review, zip batch
review, and the stored review history."""
import ast
import io
import json
import re
import sys
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from core_lib.utils import get_platform_root

router = APIRouter()

def _extract_python_sections(code_snippet: str) -> List[Dict[str, Any]]:
    """Extract functions/classes from Python code using the AST.

    Returns a list of sections with stable IDs, line ranges, and
    previews that the frontend can use for per-function review.
    """
    try:
        tree = ast.parse(code_snippet)
    except SyntaxError:
        return []

    lines = code_snippet.splitlines()
    sections: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            kind = "function"
            name = node.name
        elif isinstance(node, ast.AsyncFunctionDef):
            kind = "async function"
            name = node.name
        elif isinstance(node, ast.ClassDef):
            kind = "class"
            name = node.name
        else:
            continue

        start_line = getattr(node, "lineno", None) or 1
        end_line = getattr(node, "end_lineno", None) or start_line

        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        preview_lines = lines[start_idx:end_idx]
        preview = "\n".join(preview_lines).strip()
        if not preview:
            continue

        sections.append(
            {
                "id": f"{kind}:{name}:{start_line}",
                "name": name,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
                "preview": preview,
            }
        )

    sections.sort(key=lambda s: (s["start_line"], s["name"]))
    return sections


def _extract_code_sections(code_snippet: str, language: str) -> List[Dict[str, Any]]:
    """Extract code sections (functions/classes) for the given language.

    For Python this uses the AST for precise function/class ranges.
    For other languages it falls back to lightweight regex heuristics.
    """
    language = (language or "").lower().strip()
    if not code_snippet.strip():
        return []

    if language == "python":
        return _extract_python_sections(code_snippet)

    # For HTML/HTM, treat the content as a Vue/JS script and
    # focus on top-level methods inside the `methods:` block so
    # we don't overwhelm the UI with every inline if/for.
    if language in ("html", "htm"):
        lines = code_snippet.splitlines()
        sections: List[Dict[str, Any]] = []

        reserved_names = {"if", "for", "while", "switch", "try", "catch", "finally"}

        brace_depth = 0
        inside_methods = False

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track brace depth roughly so we know when we've
            # left the methods block.
            brace_depth += line.count("{")
            brace_depth -= line.count("}")

            if "methods:" in stripped:
                inside_methods = True
                # Methods block will start at the next opening brace
                continue

            if inside_methods and brace_depth <= 0:
                inside_methods = False

            if not inside_methods:
                continue

            # Vue-style method definitions: loadTools() { ... }
            m = re.search(r"^\s*(async\s+)?([A-Za-z0-9_$]+)\s*\([^)]*\)\s*\{", line)
            if not m:
                continue

            name = m.group(2).strip()
            if not name or name in reserved_names:
                continue

            start_line = idx
            end_line = min(len(lines), idx + 60)
            body = "\n".join(lines[idx - 1:end_line]).strip()
            if not body:
                continue

            sections.append(
                {
                    "id": f"method:{name}:{start_line}",
                    "name": name,
                    "kind": "method",
                    "label": f"methods.{name}",
                    "start_line": start_line,
                    "end_line": end_line,
                    "preview": body,
                }
            )

        sections.sort(key=lambda s: (s["start_line"], s["name"]))
        return sections

    lines = code_snippet.splitlines()
    sections: List[Dict[str, Any]] = []

    def add_regex_sections(pattern: str, kind: str) -> None:
        compiled = re.compile(pattern)
        for idx, line in enumerate(lines, start=1):
            match = compiled.search(line)
            if not match:
                continue
            name = match.group(1).strip()
            start_line = idx
            # Capture a reasonable slice of the function/body below the definition.
            end_line = min(len(lines), idx + 40)
            preview_lines = lines[idx - 1:end_line]
            preview = "\n".join(preview_lines).strip()
            if not preview:
                continue
            sections.append(
                {
                    "id": f"{kind}:{name}:{start_line}",
                    "name": name,
                    "kind": kind,
                    "start_line": start_line,
                    "end_line": end_line,
                    "preview": preview,
                }
            )

    # JavaScript/TypeScript: extract named functions and simple
    # const-as-function patterns, but skip obvious control-flow
    # names to avoid noise.
    if language in ("javascript", "js", "ts"):
        reserved_names = {"if", "for", "while", "switch", "try", "catch", "finally"}
        # Named functions: function foo(...) {
        def add_js_sections(pattern: str, kind: str) -> None:
            compiled = re.compile(pattern)
            for idx, line in enumerate(lines, start=1):
                match = compiled.search(line)
                if not match:
                    continue
                name = match.group(1).strip()
                if not name or name in reserved_names:
                    continue
                start_line = idx
                end_line = min(len(lines), idx + 40)
                preview_lines = lines[idx - 1:end_line]
                preview = "\n".join(preview_lines).strip()
                if not preview:
                    continue
                sections.append(
                    {
                        "id": f"{kind}:{name}:{start_line}",
                        "name": name,
                        "kind": kind,
                        "start_line": start_line,
                        "end_line": end_line,
                        "preview": preview,
                    }
                )

        add_js_sections(r"\bfunction\s+([A-Za-z0-9_$]+)\s*\(", "function")
        # Simple const foo = (...) patterns
        add_js_sections(r"\bconst\s+([A-Za-z0-9_$]+)\s*=\s*\(", "function")
    elif language == "go":
        add_regex_sections(r"\bfunc\s+([A-Za-z0-9_]+)\s*\(", "function")
    elif language == "bash":
        add_regex_sections(r"\b([A-Za-z0-9_]+)\s*\(\)\s*\{", "function")
    elif language == "sql":
        add_regex_sections(r"\bCREATE\s+(?:FUNCTION|PROCEDURE)\s+([A-Za-z0-9_]+)", "procedure")

    sections.sort(key=lambda s: (s["start_line"], s["name"]))
    return sections



@router.post("/api/code-review/fix", tags=["AI Analysis"])
def fix_code(payload: dict):
    # Generate fixed/improved version of code based on review.
    try:
        sys.path.insert(0, get_platform_root())
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', '').strip()  # empty = auto-resolve
        
        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")
        
        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")
        
        prompt = (
            "Fix and improve this {language} code. Return ONLY the corrected code in a code block, no explanations:\n\n"
            "```{language}\n"
            "{code}\n"
            "```"
        ).format(language=language, code=code_snippet)
        
        result = client.generate(prompt, model=model)
        
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])
        
        fixed_code = result["response"] or ""
        if "```" in fixed_code:
            parts = fixed_code.split("```")
            if len(parts) >= 2:
                fixed_code = parts[1].replace(f"{language}\n", "", 1).strip()
        
        return {"success": True, "fixed_code": fixed_code, "language": language, "model": model}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code-review", tags=["AI Analysis"])
def code_review(payload: dict):
    # Review code using local Ollama model and store results in database.

    # Payload shape:
    # {
    #     "code_snippet": "<code to review>",
    #     "language": "python" (optional, defaults to python),
    #     "model": "<tag>" (optional; empty/auto-resolves an installed model)
    #     "instructions": "Optional focus or question for the review"
    # }
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', '').strip()  # empty = auto-resolve
        instructions = payload.get('instructions', '').strip()
        
        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")
        
        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")
        
        # Build review prompt tuned for concrete changes rather than generic commentary.
        focus_block = ""
        if instructions:
            focus_block = f"\nAdditional focus/instructions from the analyst:\n{instructions}\n"

        prompt = (
            f"You are an expert {language} engineer.\n\n"
            "The user has provided a code fragment or file context and may also provide\n"
            "explicit instructions about what to change. Your job is to propose\n"
            "CONCRETE code edits, not a generic data or project review.\n\n"
            "For the code below, respond with:\n"
            "1. A very short summary of what you will change.\n"
            "2. Specific code edits:\n"
            "   - Mention the file or component name when possible.\n"
            "   - Show before/after or replacement snippets as needed.\n"
            "   - Focus on the minimal diff that satisfies the instructions.\n"
            "3. If you suggest config/UI changes (HTML/JS/Python), include the exact\n"
            "   updated snippet ready to paste into the file.\n\n"
            "Avoid broad \"data quality\" or \"potential uses\" essays. Stay focused on\n"
            "actionable code changes and patches.\n"
            f"{focus_block}\n\n"
            "Code:\n"
            f"```{language}\n"
            f"{code_snippet}\n"
            "```\n"
        )
        
        result = client.generate(prompt, model=model)
        
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])
        
        review_text = result["response"] or ""
        
        # Store in database
        db = SessionLocal()
        code_review = CodeReview(
            code_snippet=code_snippet,
            language=language,
            review_result=review_text,
            model_name=model
        )
        db.add(code_review)
        db.commit()
        db.refresh(code_review)
        db.close()
        
        return {
            "success": True,
            "review_id": code_review.id,
            "language": language,
            "model": model,
            "review": review_text,
            "created_at": code_review.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code-review/sections", tags=["AI Analysis"])
def code_review_sections(payload: dict):
    # Detect functions/classes/sections in a code snippet.
    # This is a lightweight helper for the Code Review UI and does not
    # call any models. It simply inspects the code and returns structural
    # sections so the frontend can focus reviews on specific functions or
    # classes without manual search terms.
    try:
        code_snippet = (payload.get("code_snippet") or "").strip()
        language = (payload.get("language") or "python").strip()

        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")

        sections = _extract_code_sections(code_snippet, language)

        return {
            "language": language,
            "sections": sections,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code-review/zip", tags=["AI Analysis"])
async def code_review_zip(
    file: UploadFile = File(...),
    language: str = Query("python"),
    model: str = Query(""),  # empty = auto-resolve installed model
    instructions: str = Query("", description="Optional focus or question for the review"),
):
    # Review a zipped project using the local Ollama model.
    # This endpoint accepts a .zip archive, extracts a curated subset of
    # text/code files, concatenates representative snippets, and forwards
    # the aggregated project context into the standard code_review flow.
    try:
        filename = (file.filename or "").lower()
        if not filename.endswith(".zip"):
            raise HTTPException(status_code=400, detail="Uploaded file must be a .zip archive")

        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Uploaded archive is empty")

        # Sensible limits to avoid overwhelming the model context
        max_total_chars = 20000
        max_per_file_chars = 2000

        allowed_exts = (
            ".py",
            ".js",
            ".ts",
            ".go",
            ".sh",
            ".sql",
            ".html",
            ".htm",
            ".css",
            ".json",
            ".md",
        )

        excluded_paths = [
            "node_modules/",
            "venv/",
            "env/",
            "__pycache__/",
            ".git/",
            "dist/",
            "build/",
        ]

        aggregated_chunks: list[str] = []
        total_chars = 0

        try:
            with zipfile.ZipFile(io.BytesIO(contents)) as zf:
                for name in sorted(zf.namelist()):
                    # Skip directories and obviously unwanted paths
                    if name.endswith("/"):
                        continue
                    lower_name = name.lower()
                    if any(excl in lower_name for excl in excluded_paths):
                        continue
                    if not any(lower_name.endswith(ext) for ext in allowed_exts):
                        continue

                    try:
                        with zf.open(name) as f:
                            raw_bytes = f.read()
                    except Exception:
                        continue

                    try:
                        text = raw_bytes.decode("utf-8", errors="ignore")
                    except Exception:
                        continue

                    if not text.strip():
                        continue

                    snippet = text[:max_per_file_chars]
                    chunk = f"File: {name}\n" + snippet.strip() + "\n\n"

                    if total_chars + len(chunk) > max_total_chars:
                        break

                    aggregated_chunks.append(chunk)
                    total_chars += len(chunk)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid or corrupted zip archive")

        if not aggregated_chunks:
            raise HTTPException(status_code=400, detail="No supported text/code files found in archive")

        project_summary = "\n".join(aggregated_chunks)

        # Reuse the existing code review pipeline to store and analyze
        payload = {
            "code_snippet": project_summary,
            "language": language,
            "model": model,
            "instructions": instructions,
        }
        return code_review(payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/code-reviews", tags=["AI Analysis"])
def list_code_reviews(limit: int = Query(20, ge=1, le=100)):
    # List recent code reviews from database.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        
        db = SessionLocal()
        reviews = db.query(CodeReview).order_by(
            CodeReview.created_at.desc()
        ).limit(limit).all()
        db.close()
        
        return [
            {
                "id": r.id,
                "language": r.language,
                "model": r.model_name,
                "code_snippet": r.code_snippet[:500],  # First 500 chars
                "review": r.review_result[:1000],  # First 1000 chars
                "created_at": r.created_at.isoformat()
            }
            for r in reviews
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/code-reviews/{review_id}", tags=["AI Analysis"])
def get_code_review(review_id: int):
    # Get a specific code review by ID.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        
        db = SessionLocal()
        review = db.query(CodeReview).filter(CodeReview.id == review_id).first()
        db.close()
        
        if not review:
            raise HTTPException(status_code=404, detail=f"Review {review_id} not found")
        
        return {
            "id": review.id,
            "language": review.language,
            "model": review.model_name,
            "code_snippet": review.code_snippet,
            "review": review.review_result,
            "created_at": review.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
