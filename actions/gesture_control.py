import sys
import json
import threading
import time
from pathlib import Path

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mediapipe as mp
    _MP = True
except ImportError:
    _MP = False

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE           = get_base_dir()
_CONFIG_PATH    = _BASE / "config" / "api_keys.json"

_gesture_thread: threading.Thread | None = None
_stop_event     = threading.Event()

# ── Gesture → Action map ─────────────────────────────────────────────────────
# Each gesture is identified by counting extended fingers (thumb excluded from count)
# and specific landmark positions.
_GESTURE_ACTIONS = {
    "fist":         "scroll_down",      # 0 fingers — scroll down
    "one":          "scroll_up",        # 1 finger  — scroll up
    "two":          "switch_window",    # 2 fingers — Alt+Tab
    "three":        "screenshot",       # 3 fingers — screenshot
    "four":         "minimize",         # 4 fingers — minimize window
    "open":         "maximize",         # 5 fingers — maximize window
    "thumbs_up":    "volume_up",        # thumb up  — volume up
    "thumbs_down":  "volume_down",      # thumb down — volume down
    "pinch":        "zoom_in",          # pinch     — zoom in
}

_COOLDOWN_SEC = 1.2   # minimum seconds between two gesture triggers


def _count_fingers(hand_landmarks) -> int:
    """Count extended fingers (index to pinky)."""
    tips  = [8, 12, 16, 20]
    pips  = [6, 10, 14, 18]
    count = 0
    for tip, pip in zip(tips, pips):
        if hand_landmarks.landmark[tip].y < hand_landmarks.landmark[pip].y:
            count += 1
    return count


def _is_thumb_up(hand_landmarks) -> bool:
    thumb_tip  = hand_landmarks.landmark[4]
    thumb_ip   = hand_landmarks.landmark[3]
    index_mcp  = hand_landmarks.landmark[5]
    return thumb_tip.y < thumb_ip.y and thumb_tip.y < index_mcp.y


def _is_thumb_down(hand_landmarks) -> bool:
    thumb_tip = hand_landmarks.landmark[4]
    thumb_ip  = hand_landmarks.landmark[3]
    wrist     = hand_landmarks.landmark[0]
    return thumb_tip.y > thumb_ip.y and thumb_tip.y > wrist.y


def _is_pinch(hand_landmarks) -> bool:
    thumb  = hand_landmarks.landmark[4]
    index  = hand_landmarks.landmark[8]
    dist   = ((thumb.x - index.x) ** 2 + (thumb.y - index.y) ** 2) ** 0.5
    return dist < 0.06


def _classify_gesture(hand_landmarks) -> str:
    if _is_pinch(hand_landmarks):
        return "pinch"
    if _is_thumb_up(hand_landmarks):
        return "thumbs_up"
    if _is_thumb_down(hand_landmarks):
        return "thumbs_down"
    count = _count_fingers(hand_landmarks)
    names = ["fist", "one", "two", "three", "four", "open"]
    return names[min(count, 5)]


def _execute_gesture_action(gesture: str, speak=None) -> None:
    if not _PYAUTOGUI:
        return
    action = _GESTURE_ACTIONS.get(gesture)
    if not action:
        return

    print(f"[Gesture] 🖐️  {gesture} → {action}")

    action_map = {
        "scroll_down":    lambda: pyautogui.scroll(-400),
        "scroll_up":      lambda: pyautogui.scroll(400),
        "switch_window":  lambda: pyautogui.hotkey("alt", "tab"),
        "screenshot":     lambda: pyautogui.hotkey("win", "shift", "s"),
        "minimize":       lambda: pyautogui.hotkey("win", "down"),
        "maximize":       lambda: pyautogui.hotkey("win", "up"),
        "volume_up":      lambda: [pyautogui.press("volumeup") for _ in range(5)],
        "volume_down":    lambda: [pyautogui.press("volumedown") for _ in range(5)],
        "zoom_in":        lambda: pyautogui.hotkey("ctrl", "equal"),
    }

    fn = action_map.get(action)
    if fn:
        fn()
        if speak:
            speak(f"Gesture: {gesture}")


def _gesture_loop(speak=None) -> None:
    if not _CV2 or not _MP:
        print("[Gesture] ❌ opencv-python or mediapipe not installed.")
        return

    mp_hands  = mp.solutions.hands
    hands_sol = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.6,
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[Gesture] ❌ Could not open webcam.")
        return

    last_trigger = 0.0
    last_gesture = ""
    print("[Gesture] ✅ Gesture control active. Show hand to webcam.")

    while not _stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results   = hands_sol.process(frame_rgb)

        if results.multi_hand_landmarks:
            gesture = _classify_gesture(results.multi_hand_landmarks[0])
            now     = time.time()

            if gesture != last_gesture or (now - last_trigger) > _COOLDOWN_SEC:
                if (now - last_trigger) > _COOLDOWN_SEC:
                    _execute_gesture_action(gesture, speak=speak)
                    last_trigger = now
                    last_gesture = gesture

        time.sleep(0.05)

    cap.release()
    hands_sol.close()
    print("[Gesture] 🛑 Stopped.")


def gesture_control(parameters: dict = None, player=None, speak=None) -> str:
    global _gesture_thread, _stop_event

    params = parameters or {}
    action = params.get("action", "start").lower()

    if action == "start":
        if not _CV2:
            return "opencv-python not installed. Run: pip install opencv-python"
        if not _MP:
            return "mediapipe not installed. Run: pip install mediapipe"
        if not _PYAUTOGUI:
            return "pyautogui not installed. Run: pip install pyautogui"

        if _gesture_thread and _gesture_thread.is_alive():
            return "Gesture control already running."

        _stop_event.clear()
        _gesture_thread = threading.Thread(
            target=_gesture_loop, kwargs={"speak": speak}, daemon=True
        )
        _gesture_thread.start()
        return (
            "Gesture control active! Webcam se haath dikhao:\n"
            "✊ Fist=Scroll Down  ☝️ 1 Finger=Scroll Up  ✌️ 2=Alt+Tab\n"
            "3 Fingers=Screenshot  4=Minimize  🖐️ 5=Maximize\n"
            "👍 Thumbs Up=Vol+  👎 Thumbs Down=Vol-  🤏 Pinch=Zoom In"
        )

    if action == "stop":
        _stop_event.set()
        return "Gesture control stopped."

    if action == "status":
        running = bool(_gesture_thread and _gesture_thread.is_alive())
        return f"Gesture control is {'active ✅' if running else 'inactive ❌'}."

    if action == "map":
        lines = [f"  {g:12s} → {a}" for g, a in _GESTURE_ACTIONS.items()]
        return "Current gesture map:\n" + "\n".join(lines)

    return f"Unknown action: {action}. Use: start | stop | status | map"
