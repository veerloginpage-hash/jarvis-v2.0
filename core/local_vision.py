"""Fast optional local SmolVLM backend for screenshots and camera frames."""
from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "models" / "SmolVLM-256M-Instruct-Q8_0.gguf"
MMPROJ_PATH = BASE_DIR / "models" / "mmproj-SmolVLM-256M-Instruct-Q8_0.gguf"

_model = None
_load_lock = threading.Lock()
_last_error = None


def is_available() -> bool:
    return MODEL_PATH.is_file() and MMPROJ_PATH.is_file()


def _get_model():
    global _model, _last_error
    if _model is not None:
        return _model
    if not is_available():
        raise RuntimeError("Local SmolVLM model files are missing")
    with _load_lock:
        if _model is not None:
            return _model
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            _last_error = exc
            raise RuntimeError("llama-cpp-python is not installed") from exc

        kwargs = dict(
            model_path=str(MODEL_PATH),
            clip_model_path=str(MMPROJ_PATH),
            chat_format="chatml",
            n_ctx=2048,
            n_batch=512,
            n_threads=max(2, min(8, os.cpu_count() or 4)),
            verbose=False,
        )
        try:
            # Offload to GPU when the installed llama.cpp build supports it.
            _model = Llama(**kwargs, n_gpu_layers=-1)
        except Exception:
            # CPU-only wheels remain fully supported.
            _model = Llama(**kwargs, n_gpu_layers=0)
        return _model


def analyze(image_bytes: bytes, question: str, max_tokens: int = 220) -> str:
    """Analyze one image locally and return concise text."""
    # Full desktop screenshots are often 1080p/4K. Downscale before encoding
    # to keep local inference responsive while preserving readable UI text.
    try:
        from PIL import Image
        from io import BytesIO
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        if max(image.size) > 1280:
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buf = BytesIO()
            image.save(buf, format="JPEG", quality=82, optimize=True)
            image_bytes = buf.getvalue()
    except Exception:
        pass
    encoded = base64.b64encode(image_bytes).decode("ascii")
    result = _get_model().create_chat_completion(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ],
        }],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    text = result["choices"][0]["message"].get("content", "")
    if isinstance(text, list):
        text = " ".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
    return str(text).strip()


def warmup() -> bool:
    """Load the model in the background so the first real request is quicker."""
    try:
        _get_model()
        return True
    except Exception as exc:
        global _last_error
        _last_error = exc
        return False


def status() -> str:
    if not is_available():
        return "model files missing"
    if _model is not None:
        return "ready"
    return "runtime unavailable" if _last_error else "cold"
