"""Cheap runtime health snapshot; no network calls and safe to call frequently."""
from __future__ import annotations

import importlib.util
import json
import platform
from pathlib import Path


def snapshot(base_dir: str | Path) -> dict:
    root = Path(base_dir)
    config = root / "config" / "api_keys.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {
        "api": bool(data.get("gemini_api_key")),
        "local_vision": (root / "models" / "SmolVLM-256M-Instruct-Q8_0.gguf").is_file(),
        "playwright": importlib.util.find_spec("playwright") is not None,
        "pyautogui": importlib.util.find_spec("pyautogui") is not None,
        "microphone": importlib.util.find_spec("sounddevice") is not None,
        "offline_tts": importlib.util.find_spec("pyttsx3") is not None,
    }


def detailed_snapshot(base_dir: str | Path) -> dict:
    """Return safe, user-facing diagnostics without exposing credentials."""
    result = snapshot(base_dir)
    result["platform"] = platform.platform()
    try:
        from core import local_vision
        result["local_vision_status"] = local_vision.status()
    except Exception as exc:
        result["local_vision_status"] = f"unavailable: {str(exc)[:100]}"
    try:
        import sounddevice as sd
        result["audio_input"] = str(sd.query_devices(kind="input").get("name", "unknown"))
        result["audio_output"] = str(sd.query_devices(kind="output").get("name", "unknown"))
    except Exception as exc:
        result["audio_error"] = str(exc)[:160]
    try:
        from core.audio_devices import describe
        result["audio_devices"] = describe()
    except Exception:
        result["audio_devices"] = {"inputs": [], "outputs": []}
    result["audio_preferences"] = {
        "input": data.get("audio_input_device", ""),
        "output": data.get("audio_output_device", ""),
    }
    return result


def format_snapshot(data: dict) -> str:
    labels = {
        "api": "Gemini API", "local_vision": "Local vision", "playwright": "Browser engine",
        "pyautogui": "Desktop control", "microphone": "Audio runtime",
        "offline_tts": "Offline voice fallback",
    }
    lines = [f"{label}: {'READY' if data.get(key) else 'MISSING'}" for key, label in labels.items()]
    for key in ("audio_input", "audio_output", "platform", "audio_error"):
        if data.get(key):
            lines.append(f"{key.replace('_', ' ').title()}: {data[key]}")
    if data.get("local_vision_status"):
        lines.append(f"Local vision runtime: {data['local_vision_status']}")
    return "\n".join(lines)
