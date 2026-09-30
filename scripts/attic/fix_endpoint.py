import re

# Read the file
with open('api/main.py', 'r') as f:
    content = f.read()

# Add the AnalyzeRequest class after ToolInfo
old_text = """class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None

# Helper Functions"""

new_text = """class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None

class AnalyzeRequest(BaseModel):
    case_id: str
    model: str = "llama2"
    context: str = ""

# Helper Functions"""

content = content.replace(old_text, new_text)

# Fix the analyze_case function signature
old_func = """@app.post(\"/api/db/analyze\", tags=[\"Database\"])
def analyze_case(request: dict):
    \"\"\"Analyze a case using Ollama LLM.\"\"\"
    try:
        # Extract from request body
        case_id = request.get('case_id') if isinstance(request, dict) else request
        model = request.get('model', 'llama2') if isinstance(request, dict) else 'llama2'
        context = request.get('context', '') if isinstance(request, dict) else ''"""

new_func = """@app.post(\"/api/db/analyze\", tags=[\"Database\"])
def analyze_case(request: AnalyzeRequest):
    \"\"\"Analyze a case using Ollama LLM.\"\"\"
    try:
        case_id = request.case_id
        model = request.model
        context = request.context"""

content = content.replace(old_func, new_func)

# Write back
with open('api/main.py', 'w') as f:
    f.write(content)

print("Fixed analyze_case endpoint")
