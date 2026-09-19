# actions/ai_screen_vision.py
import time
import io
import re
import threading
from PIL import Image
from actions.screen_processor import _capture_screen, _capture_camera
from core.ai_providers import get_router, HuggingFaceProvider
from core import local_vision

# Global state for screen monitoring
_monitoring_thread = None
_monitoring_stop = threading.Event()

def run_florence_ocr(image_bytes: bytes) -> str:
    """Uses HF Florence-2 model for high-accuracy OCR."""
    try:
        local_text = local_vision.analyze(
            image_bytes,
            "Transcribe all clearly visible text exactly. Return only the text; if none is visible, return NONE.",
            max_tokens=300,
        )
        if local_text and local_text.upper() != "NONE":
            return local_text
    except Exception as e:
        print(f"[AI Screen Vision] Local OCR unavailable: {e}")

    try:
        hf = HuggingFaceProvider()
        # Florence-2 large model ID is microsoft/Florence-2-large
        # To perform OCR, we send the image and prompt '<OCR>' or '<OCR_WITH_REGION>'
        import base64
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        payload = {
            "inputs": img_b64,
            "parameters": {"task": "ocr"}
        }
        # In case the direct HF hub endpoint doesn't support the raw inputs format directly,
        # we can fallback to using the generic smart router's vision capacity.
        # But let's write a standard HF model call or fallback to OpenRouter VL.
        res = hf.query("microsoft/Florence-2-large", {"inputs": img_b64})
        if isinstance(res, dict) and "generated_text" in res:
            return res["generated_text"]
        elif isinstance(res, list) and len(res) > 0 and "generated_text" in res[0]:
            return res[0]["generated_text"]
    except Exception as e:
        print(f"[AI Screen Vision] Florence-2 OCR failed: {e}. Falling back to OpenRouter vision...")
        
    # Fallback to OpenRouter Qwen VL
    router = get_router()
    return router.run_vision(image_bytes, "Read and transcribe all visible text in this image precisely. Return only the text.")

def monitor_screen_loop(target_text: str, interval: int, player, speak):
    global _monitoring_thread
    print(f"[Screen Monitor] Monitoring started for text: '{target_text}' every {interval}s")
    last_seen = False
    errors = 0

    while not _monitoring_stop.is_set():
        try:
            img_bytes, mime = _capture_screen()
            ocr_text = run_florence_ocr(img_bytes)

            found = target_text.lower() in ocr_text.lower()
            # Alert only on a new appearance; don't spam the user every poll.
            if found and not last_seen:
                msg = f"Alert! Screen par target text '{target_text}' find ho gaya hai."
                if player:
                    player.write_log(f"ALERT: '{target_text}' found!")
                if speak:
                    speak(msg)
                break
            last_seen = found
            errors = 0
        except Exception as e:
            errors += 1
            print(f"[Screen Monitor] Loop error: {e}")

        if _monitoring_stop.wait(max(2, min(int(interval), 60))):
            break
    
    _monitoring_stop.clear()
    _monitoring_thread = None

def ai_screen_vision(parameters: dict, player=None, speak=None) -> str:
    """
    Enhanced screen and image understanding actions.
    parameters:
      action: analyze_screen | analyze_camera | ocr | start_monitor | stop_monitor
      text: description or question about screen/image
      target_text: text to watch for in monitor mode
      interval: polling interval in seconds (default 5)
    """
    global _monitoring_thread
    action = parameters.get("action", "").lower().strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Vision: {action}")
        
    try:
        if action == "analyze_screen":
            user_text = parameters.get("text", "Describe what is on the screen in detail.")
            if speak:
                speak("Sir, screen capture karke analyze kar rha hoon.")
            img_bytes, mime = _capture_screen()
            try:
                result = local_vision.analyze(img_bytes, user_text)
            except Exception as exc:
                print(f"[AI Screen Vision] Local fallback: {exc}")
                result = get_router().run_vision(img_bytes, user_text)
            return f"Screen Analysis:\n{result}"
            
        elif action == "analyze_camera":
            user_text = parameters.get("text", "What is in front of the camera?")
            if speak:
                speak("Sir, camera stream check kar rha hoon.")
            img_bytes, mime = _capture_camera()
            try:
                result = local_vision.analyze(img_bytes, user_text)
            except Exception as exc:
                print(f"[AI Screen Vision] Local camera vision unavailable: {exc}")
                result = get_router().run_vision(img_bytes, user_text)
            return f"Camera Analysis:\n{result}"
            
        elif action == "ocr":
            if speak:
                speak("Sir, screen text extract kar rha hoon using OCR.")
            img_bytes, mime = _capture_screen()
            text = run_florence_ocr(img_bytes)
            return f"Extracted Screen Text (OCR):\n{text}"
            
        elif action == "start_monitor":
            target = parameters.get("target_text", "")
            if not target:
                return "Please provide 'target_text' parameters to monitor."
            interval = int(parameters.get("interval", 5))
            
            if _monitoring_thread and _monitoring_thread.is_alive():
                return "Screen monitoring is already active."
            _monitoring_stop.clear()
            _monitoring_thread = threading.Thread(
                target=monitor_screen_loop,
                args=(target, interval, player, speak),
                daemon=True
            )
            _monitoring_thread.start()
            return f"Screen monitoring started for: '{target}' every {interval} seconds."
            
        elif action == "stop_monitor":
            if not _monitoring_thread or not _monitoring_thread.is_alive():
                return "Screen monitoring is not running."
            _monitoring_stop.set()
            return "Screen monitoring stopped."
            
        else:
            return f"Unknown AI Vision action: {action}"
            
    except Exception as e:
        return f"AI Screen Vision failed: {e}"
