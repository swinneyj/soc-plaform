import sys
sys.path.insert(0, '/app')

with open('/app/db/models.py', 'r') as f:
    content = f.read()

code_review_class = """
class CodeReview(Base):
    \"\"\"Code review results from Ollama LLM analysis.\"\"\"
    __tablename__ = "code_reviews"

    id = Column(Integer, primary_key=True, index=True)
    code_snippet = Column(Text)
    language = Column(String, default="python")
    review_result = Column(Text)
    model_name = Column(String, default="llama3.1:8b")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

"""

new_content = content.replace('# Create tables', code_review_class + '# Create tables')

with open('/app/db/models.py', 'w') as f:
    f.write(new_content)

print('Added CodeReview table')