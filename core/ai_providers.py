# core/ai_providers.py
import requests
import json
import threading
import time
from config import get_config


class GroqProvider:
    """Fast OpenAI-compatible Groq client with round-robin key failover."""
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self):
        config = get_config()
        keys = config.get("groq_api_keys", [])
        if isinstance(keys, str):
            keys = [keys]
        self.keys = [str(k).strip() for k in keys if str(k).strip()]
        self._index = 0
        self._lock = threading.Lock()

    def _ordered_keys(self):
        if not self.keys:
            return []
        with self._lock:
            start = self._index % len(self.keys)
            self._index += 1
        return self.keys[start:] + self.keys[:start]

    def chat(self, model: str, messages: list, temperature: float = 0.4,
             max_tokens: int = 1200) -> str:
        payload = {"model": model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        last_error = None
        for key in self._ordered_keys():
            try:
                response = requests.post(
                    self.URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    timeout=(3.5, 18),
                )
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"]
                last_error = RuntimeError(f"Groq {response.status_code}: {response.text[:160]}")
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError("No Groq API key configured")

class OpenRouterProvider:
    """Manages calls to OpenRouter API using free-tier models."""
    def __init__(self):
        config = get_config()
        self.api_key = config.get("openrouter_api_key", "")
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

    def chat(self, model: str, messages: list, temperature: float = 0.7,
             max_tokens: int = 2048, attempts: int = 3,
             timeout: tuple[float, float] = (3.5, 30)) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/FatihMakes/Veer-Indus",
            "X-Title": "Veer Indus JARVIS"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        for attempt in range(max(1, int(attempts))):
            try:
                response = requests.post(self.base_url, json=payload, headers=headers, timeout=timeout)
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    error_msg = response.text
                    print(f"[OpenRouter] Error {response.status_code} on attempt {attempt+1}: {error_msg}")
                    if attempt + 1 < attempts:
                        time.sleep(1)
            except Exception as e:
                print(f"[OpenRouter] Exception on attempt {attempt+1}: {e}")
                if attempt + 1 < attempts:
                    time.sleep(1)
                
        raise Exception("OpenRouter API call failed after 3 attempts.")

class HuggingFaceProvider:
    """Manages calls to Hugging Face Inference API."""
    def __init__(self):
        config = get_config()
        keys = config.get("huggingface_api_keys", [])
        if not keys:
            keys = [config.get("huggingface_api_key", "")]
        if isinstance(keys, str):
            keys = [keys]
        self.tokens = [str(k).strip() for k in keys if str(k).strip()]
        self._index = 0
        self._lock = threading.Lock()
        self.api_url_base = "https://api-inference.huggingface.co/models/"

    def _ordered_tokens(self):
        if not self.tokens:
            return []
        with self._lock:
            start = self._index % len(self.tokens)
            self._index += 1
        return self.tokens[start:] + self.tokens[:start]

    def query(self, model: str, payload: dict, is_binary: bool = False) -> bytes or dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{self.api_url_base}{model}"
        
        last_error = None
        for token in self._ordered_tokens():
            try:
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.post(url, headers=headers, json=payload, timeout=(3.5, 25))
                if response.status_code == 200:
                    if is_binary:
                        return response.content
                    return response.json()
                elif response.status_code == 503:
                    # Model loading, wait and retry
                    last_error = RuntimeError(f"HF model loading: {model}")
                else:
                    last_error = RuntimeError(f"HF {response.status_code}: {response.text[:160]}")
            except Exception as e:
                last_error = e
        raise last_error or Exception(f"HuggingFace API call to {model} failed.")

class SmartRouter:
    """Routes requests to the best available free model on OpenRouter or HuggingFace."""
    def __init__(self):
        self.groq_provider = GroqProvider()
        self.or_provider = OpenRouterProvider()
        self.hf_provider = HuggingFaceProvider()

    def generate_text(self, prompt: str, task_type: str = "general", system_prompt: str = None) -> str:
        """
        Generates text using the best model for the task.
        task_type options: 'general', 'coding', 'reasoning', 'vision'
        """
        # Groq is first for latency; model selection distributes work by task.
        if task_type == "coding":
            models = [
                "openai/gpt-oss-20b",
                "qwen/qwen3.6-27b",
            ]
        elif task_type == "reasoning":
            models = [
                "openai/gpt-oss-120b",
                "qwen/qwen3.6-27b",
            ]
        else:
            models = [
                "openai/gpt-oss-20b",
                "qwen/qwen3.6-27b",
            ]

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        last_error = None
        for model in models:
            try:
                print(f"[SmartRouter] ⚡ Groq {model} for {task_type}")
                return self.groq_provider.chat(model, messages, max_tokens=1200)
            except Exception as e:
                print(f"[SmartRouter] Groq {model} failed: {e}")
                last_error = e
        # Provider-level fallbacks retain compatibility with existing features.
        for model in ("openrouter/free", "google/gemma-4-31b-it:free"):
            try:
                return self.or_provider.chat(model, messages, max_tokens=1200)
            except Exception as e:
                print(f"[SmartRouter] Fallback {model} failed: {e}")
                last_error = e
        raise last_error or Exception("All routed models failed.")

    def run_vision(self, image_bytes: bytes, question: str) -> str:
        """Runs a visual analysis on an image using Qwen2.5-VL or Gemma-4."""
        import base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ]
        
        # Vision is latency-sensitive. One short attempt per provider avoids
        # turning a failed/rate-limited fallback into a 30–90 second hang.
        models = ["openrouter/free", "google/gemma-4-31b-it:free"]
        
        for model in models:
            try:
                return self.or_provider.chat(
                    model, messages, max_tokens=180, attempts=1, timeout=(2.5, 7)
                )
            except Exception as e:
                print(f"[SmartRouter] Vision model {model} failed: {e}")
                continue
                
        raise Exception("All vision models failed to process the image.")

# Singleton helper
_router = None
def get_router():
    global _router
    if _router is None:
        _router = SmartRouter()
    return _router
