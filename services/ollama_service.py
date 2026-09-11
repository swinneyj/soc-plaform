"""
Ollama integration for local LLM analysis.
Provides functions to call Ollama models and cache responses.
"""

import requests
import json
import time
from typing import Optional, Dict, Any
import os

OLLAMA_BASE_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
DEFAULT_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.1:8b')

# Allow the request timeout to be tuned via environment; default to a
# generous window so larger models (e.g., 8B variants) can complete.
try:
    _timeout_env = os.environ.get('OLLAMA_TIMEOUT')
    OLLAMA_REQUEST_TIMEOUT = int(_timeout_env) if _timeout_env else 300
except ValueError:
    OLLAMA_REQUEST_TIMEOUT = 300

try:
    _num_predict_env = os.environ.get('OLLAMA_NUM_PREDICT')
    # Keep assessments bounded: the UI needs a concise decision and a few
    # grounded follow-up queries, not an unbounded essay.
    OLLAMA_NUM_PREDICT = int(_num_predict_env) if _num_predict_env else 500
except ValueError:
    OLLAMA_NUM_PREDICT = 500

class OllamaClient:
    """Simple Ollama client for local LLM inference."""
    
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or os.environ.get('OLLAMA_URL') or OLLAMA_BASE_URL).rstrip('/')
        self.model = model or os.environ.get('OLLAMA_MODEL') or DEFAULT_MODEL
        self.available = False
        self.refresh()

    def refresh(self) -> bool:
        """Refresh connectivity instead of retaining a stale startup result."""
        self.available = self._check_connection()
        return self.available
    
    def _check_connection(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except:
            return False
    
    def list_models(self) -> list:
        """List available models on Ollama."""
        self.refresh()
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code == 200:
                models = r.json().get('models', [])
                return [m['name'] for m in models]
        except:
            pass
        return []
    
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate text using Ollama.
        
        Args:
            prompt: The input prompt
            model: Model name (uses default if not specified)
            temperature: Sampling temperature (0.0-1.0)
        
        Returns:
            Dict with response, model, and metadata
        """
        model = model or self.model
        
        if not self.available:
            return {
                "success": False,
                "error": f"Ollama not available at {self.base_url}. Is it running? Start with: ollama serve",
                "response": None
            }
        
        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False
            }
            merged_options = dict(options or {})
            merged_options.setdefault("temperature", temperature)
            merged_options.setdefault("num_predict", OLLAMA_NUM_PREDICT)
            if merged_options:
                payload["options"] = merged_options
            
            started_at = time.monotonic()
            print(
                f"[ollama] generate start model={model} prompt_chars={len(prompt)} "
                f"max_tokens={merged_options.get('num_predict')}",
                flush=True,
            )
            r = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=OLLAMA_REQUEST_TIMEOUT
            )
            
            if r.status_code == 200:
                data = r.json()
                elapsed = time.monotonic() - started_at
                print(
                    f"[ollama] generate complete model={model} elapsed={elapsed:.1f}s "
                    f"eval_tokens={data.get('eval_count', 0)}",
                    flush=True,
                )
                return {
                    "success": True,
                    "response": data.get('response', ''),
                    "model": data.get('model', model),
                    "tokens": data.get('eval_count', 0),
                    "prompt_eval_count": data.get('prompt_eval_count', 0),
                    "total_duration_ns": data.get('total_duration', 0),
                    "load_duration_ns": data.get('load_duration', 0),
                    "eval_duration_ns": data.get('eval_duration', 0),
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "error": f"Ollama returned {r.status_code}: {r.text}",
                    "response": None
                }
        
        except requests.Timeout:
            return {
                "success": False,
                "error": f"Ollama request timed out ({OLLAMA_REQUEST_TIMEOUT}s). Model inference took too long.",
                "response": None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": None
            }
    
    def analyze_security_event(self, event_data: str, context: str = "") -> Dict[str, Any]:
        """
        Specialized function to analyze security events with structured prompt.
        
        Args:
            event_data: The raw event or alert data
            context: Additional context (e.g., "This is from Splunk ES")
        
        Returns:
            Analysis result with verdict and recommendations
        """
        prompt = f"""You are a SOC analyst. Analyze the following security event and provide:
1. Threat classification (Benign, Suspicious, Malicious)
2. Confidence level (0-100%)
3. Key indicators
4. Recommended actions

Event Data:
{event_data}

{f'Additional Context: {context}' if context else ''}

Provide a concise analysis:"""
        
        result = self.generate(prompt)
        return result

# Singleton instance
_ollama_client = None

def get_ollama_client() -> OllamaClient:
    """Get or create Ollama client."""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = OllamaClient()
    return _ollama_client

def check_ollama_health() -> Dict[str, Any]:
    """Check Ollama service health."""
    client = get_ollama_client()
    client.refresh()
    models = client.list_models()
    return {
        "available": client.available,
        "url": client.base_url,
        "models": models,
        "default_model": client.model
    }
