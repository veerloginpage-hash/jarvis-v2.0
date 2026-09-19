"""Optional local TTS fallback; never makes the main assistant depend on it."""
from __future__ import annotations

import threading

_lock = threading.Lock()


def speak(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    try:
        import pyttsx3
        with _lock:
            engine = pyttsx3.init()
            engine.setProperty("rate", 185)
            engine.say(value)
            engine.runAndWait()
            engine.stop()
        return True
    except Exception as exc:
        print(f"[OfflineVoice] unavailable: {exc}")
        return False
