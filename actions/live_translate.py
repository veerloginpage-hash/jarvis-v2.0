import json
import sys
import tempfile
import subprocess
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _ai_translate(text: str, target_lang: str, source_lang: str = "auto") -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model  = genai.GenerativeModel("gemini-2.5-flash")
    src    = f"from {source_lang} " if source_lang != "auto" else ""
    prompt = (
        f"Translate the following text {src}to {target_lang}.\n"
        f"Rules:\n"
        f"- Output ONLY the translated text, nothing else.\n"
        f"- Preserve formatting, punctuation, and tone.\n\n"
        f"Text:\n{text}"
    )
    return model.generate_content(prompt).text.strip()


def _ai_transcribe_file(file_path: str, language: str = "auto") -> str:
    """Transcribe an audio/video file using Gemini."""
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())

    path = Path(file_path)
    if not path.exists():
        return f"File not found: {file_path}"

    client = genai.upload_file(path)
    model  = genai.GenerativeModel("gemini-2.5-flash")
    lang_hint = f" The audio is in {language}." if language != "auto" else ""
    prompt = (
        f"Transcribe this audio accurately.{lang_hint} "
        f"Output ONLY the transcription text, no timestamps, no labels."
    )
    response = model.generate_content([prompt, client])
    return response.text.strip()


def _record_mic(seconds: int = 10) -> str:
    """Record mic audio and return temp file path."""
    try:
        import sounddevice as sd
        import numpy as np
        import wave

        sample_rate = 16000
        print(f"[Translate] 🎙️ Recording {seconds}s...")
        audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate,
                       channels=1, dtype="int16")
        sd.wait()

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())
        return tmp.name
    except Exception as e:
        raise RuntimeError(f"Mic recording failed: {e}")


def live_translate(parameters: dict = None, player=None, speak=None) -> str:
    params      = parameters or {}
    action      = params.get("action", "translate").lower()
    text        = params.get("text", "").strip()
    target_lang = params.get("target_lang", "English").strip()
    source_lang = params.get("source_lang", "auto").strip()
    file_path   = params.get("file_path", "").strip()
    duration    = int(params.get("duration", 10) or 10)
    save        = str(params.get("save", "false")).lower() in ("true", "1", "yes")

    # ── TRANSLATE TEXT ────────────────────────────────────────────
    if action == "translate":
        if not text:
            return "Text do translate karne ke liye."
        result = _ai_translate(text, target_lang, source_lang)
        if save:
            out = Path.home() / "Desktop" / "translation.txt"
            out.write_text(f"Original:\n{text}\n\nTranslation ({target_lang}):\n{result}", encoding="utf-8")
            return f"{result}\n\n[Saved to Desktop/translation.txt]"
        return result

    # ── TRANSCRIBE FILE ───────────────────────────────────────────
    if action == "transcribe":
        if not file_path:
            return "File path do transcribe karne ke liye."
        if speak:
            speak("Transcription shuru kar raha hun, thoda wait karo sir.")
        transcript = _ai_transcribe_file(file_path, source_lang)
        if save:
            out = Path(file_path).with_suffix(".txt")
            out.write_text(transcript, encoding="utf-8")
            return f"Transcription done.\n\n{transcript[:500]}{'...' if len(transcript) > 500 else ''}\n\n[Saved to {out}]"
        return transcript

    # ── TRANSCRIBE + TRANSLATE FILE ───────────────────────────────
    if action == "transcribe_translate":
        if not file_path:
            return "File path do."
        if speak:
            speak("File transcribe karke translate kar raha hun sir.")
        transcript  = _ai_transcribe_file(file_path, source_lang)
        translation = _ai_translate(transcript, target_lang)
        result = f"[Transcription]\n{transcript}\n\n[Translation → {target_lang}]\n{translation}"
        if save:
            out = Path(file_path).with_suffix("_translated.txt")
            out.write_text(result, encoding="utf-8")
            return result + f"\n\n[Saved to {out}]"
        return result

    # ── RECORD MIC + TRANSCRIBE ───────────────────────────────────
    if action == "record_transcribe":
        if speak:
            speak(f"{duration} second mic recording shuru ho rahi hai sir.")
        tmp_path = _record_mic(duration)
        transcript = _ai_transcribe_file(tmp_path, source_lang)
        Path(tmp_path).unlink(missing_ok=True)
        if save:
            out = Path.home() / "Desktop" / "transcription.txt"
            out.write_text(transcript, encoding="utf-8")
            return f"{transcript}\n\n[Saved to Desktop/transcription.txt]"
        return transcript

    # ── RECORD MIC + TRANSCRIBE + TRANSLATE ──────────────────────
    if action == "record_translate":
        if speak:
            speak(f"{duration} second recording lekar translate karunga sir.")
        tmp_path   = _record_mic(duration)
        transcript = _ai_transcribe_file(tmp_path, source_lang)
        Path(tmp_path).unlink(missing_ok=True)
        translation = _ai_translate(transcript, target_lang)
        result = f"[Transcription]\n{transcript}\n\n[Translation → {target_lang}]\n{translation}"
        if save:
            out = Path.home() / "Desktop" / "live_translation.txt"
            out.write_text(result, encoding="utf-8")
            return result + f"\n\n[Saved to Desktop/live_translation.txt]"
        return result

    return f"Unknown action: {action}. Use: translate, transcribe, transcribe_translate, record_transcribe, record_translate."
