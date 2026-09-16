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
DEFAULT_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.1:latest')
OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY', '').strip()

# Preference order used when auto-resolving an installed model tag. The
# historical default 'llama3.1:8b' is kept in the list so explicit legacy
# requests still resolve to something runnable instead of erroring.
MODEL_PREFERENCES = [
    'llama3.1:latest',
    'llama3.1:8b',
    'llama3.1',
]

# Allow the request timeout to be tuned via environment while failing clearly
# instead of leaving an analyst workflow spinning indefinitely.
try:
    _timeout_env = os.environ.get('OLLAMA_TIMEOUT')
    OLLAMA_REQUEST_TIMEOUT = int(_timeout_env) if _timeout_env else 90
except ValueError:
    OLLAMA_REQUEST_TIMEOUT = 90

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
        self.api_key = os.environ.get('OLLAMA_API_KEY', OLLAMA_API_KEY).strip()
        self.available = False
        self.refresh()

    def _headers(self) -> Dict[str, str]:
        """Return authentication headers when using a hosted Ollama API."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def refresh(self) -> bool:
        """Refresh connectivity instead of retaining a stale startup result."""
        self.available = self._check_connection()
        return self.available
    
    def _check_connection(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", headers=self._headers(), timeout=2)
            return r.status_code == 200
        except:
            return False
    
    def list_models(self) -> list:
        """List available models on Ollama."""
        self.refresh()
        try:
            r = requests.get(f"{self.base_url}/api/tags", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                models = r.json().get('models', [])
                return [m['name'] for m in models]
        except:
            pass
        return []

    def resolve_model(self, requested: Optional[str] = None) -> str:
        """Pick a runnable model tag.

        Resolution order:
        1. An explicitly requested tag that is actually installed.
        2. OLLAMA_MODEL env var (if installed).
        3. Best match from MODEL_PREFERENCES among installed models.
        4. Any installed model (first listed).
        5. The requested tag as-is / DEFAULT_MODEL — so the error message
           from Ollama still names a concrete tag when nothing is installed.

        This prevents the classic first-run failure where the API defaults
        to 'llama3.1:8b' but the machine only has 'llama3.1:latest' pulled.
        """
        requested = (requested or '').strip()
        installed = []
        try:
            installed = self.list_models() or []
        except Exception:
            installed = []
        installed_lower = {m.lower(): m for m in installed}

        if requested:
            if requested.lower() in installed_lower:
                return installed_lower[requested.lower()]

        env_model = (os.environ.get('OLLAMA_MODEL') or '').strip()
        if env_model and env_model.lower() in installed_lower:
            return installed_lower[env_model.lower()]

        for pref in MODEL_PREFERENCES:
            pref_l = pref.lower()
            if pref_l in installed_lower:
                return installed_lower[pref_l]
            # Family match: 'llama3.1' matches 'llama3.1:latest' etc. —
            # handled above by exact key; here also allow any tag that
            # starts with the family name.
            if any(m.lower().startswith(pref_l + ':') or m.lower() == pref_l for m in installed):
                for m in installed:
                    if m.lower().startswith(pref_l + ':') or m.lower() == pref_l:
                        return m

        if installed:
            return installed[0]

        return requested or DEFAULT_MODEL
    
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
        model = self.resolve_model(model or self.model)
        
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
                headers=self._headers(),
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
