# actions/ai_desktop_control.py
import time
import random
import io
import re
import sys
from pathlib import Path
from core.ai_providers import get_router
from core import local_vision
from actions.computer_control import _screen_find

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def calculate_bezier_points(start, end, num_points=25):
    """Generates natural bezier curve path points between start and end coordinates."""
    x0, y0 = start
    x3, y3 = end
    
    # Generate random control points to make curve look human
    control_range = 100
    x1 = x0 + (x3 - x0) * 0.25 + random.uniform(-control_range, control_range)
    y1 = y0 + (y3 - y0) * 0.25 + random.uniform(-control_range, control_range)
    x2 = x0 + (x3 - x0) * 0.75 + random.uniform(-control_range, control_range)
    y2 = y0 + (y3 - y0) * 0.75 + random.uniform(-control_range, control_range)
    
    points = []
    for i in range(num_points):
        t = i / (num_points - 1)
        x = (1-t)**3 * x0 + 3*(1-t)**2 * t * x1 + 3*(1-t) * t**2 * x2 + t**3 * x3
        y = (1-t)**3 * y0 + 3*(1-t)**2 * t * y1 + 3*(1-t) * t**2 * y2 + t**3 * y3
        points.append((int(x), int(y)))
    return points

def move_mouse_human_like(x, y):
    """Moves mouse using bezier curve and natural speed variations."""
    if not _PYAUTOGUI:
        return
    start_x, start_y = pyautogui.position()
    if start_x == x and start_y == y:
        return
    distance = ((int(x) - start_x) ** 2 + (int(y) - start_y) ** 2) ** 0.5
    # Fast but visibly natural: short targets barely pause, long moves use a
    # compact curved path with easing and no overshoot.
    duration = max(0.045, min(0.22, 0.035 + distance / 5200.0))
    points = calculate_bezier_points((start_x, start_y), (int(x), int(y)),
                                     num_points=max(5, min(16, int(distance / 90) + 5)))
    step = duration / max(1, len(points) - 1)
    for px, py in points[1:-1]:
        pyautogui.moveTo(px, py, duration=step)
    pyautogui.moveTo(int(x), int(y), duration=step)

def click_element_at(x, y, button="left", clicks=1):
    """Moves to x, y in a human-like way and clicks."""
    if not _PYAUTOGUI:
        return f"Clicked at ({x}, {y}) [pyautogui missing]"
    
    move_mouse_human_like(x, y)
    pyautogui.click(x, y, button=button, clicks=clicks)
    return f"Moved and clicked at ({x}, {y}) with button {button} ({clicks} clicks)"

def human_type(text, clear_first=False):
    """Types text quickly and deterministically for responsive automation."""
    if not _PYAUTOGUI:
        return f"Typed: {text} [pyautogui missing]"
        
    if clear_first:
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        
    # If text is very long, use clipboard for efficiency
    if len(text) > 40 and _PYPERCLIP:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        return f"Typed using clipboard: {text[:40]}..."
        
    pyautogui.write(text, interval=0.005)
        
    return f"Typed text: {text[:40]}"

def screen_find_via_ai(description: str) -> tuple[int, int] or None:
    """Takes a screenshot and sends it to the vision model to find coordinates of description."""
    if not _PYAUTOGUI:
        return None
        
    # Use the shared Gemini 2.5 coordinate finder for icon-only UI. The tiny
    # local VLM stays useful for OCR/description but is not trusted for clicks.
    return _screen_find(description)

def ai_desktop_control(parameters: dict, player=None, speak=None) -> str:
    """
    Pro-level AI-powered desktop control action.
    parameters:
      action: click | type | move | drag | hotkey | press | scroll | screenshot | screen_find_click
      text: text to type
      description: element description to find using AI vision
      x, y: coordinates (optional)
      button: left | right (default left)
      keys: hotkey combo e.g. 'ctrl+c'
      key: single key e.g. 'enter'
      direction: up | down
      amount: scroll amount (default 5)
    """
    if not _PYAUTOGUI:
        return "PyAutoGUI not installed."
        
    action = parameters.get("action", "").lower().strip()
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Desktop: {action}")
        
    try:
        if action == "screen_find_click":
            desc = parameters.get("description", "")
            if not desc:
                return "No element description provided."
            if speak:
                speak(f"Screen standard search and target find kar rha hoon for: {desc}")
            coords = screen_find_via_ai(desc)
            if coords:
                res = click_element_at(coords[0], coords[1], parameters.get("button", "left"))
                return f"Successfully found and clicked {desc} at {coords}. Details: {res}"
            else:
                return f"Could not locate '{desc}' on screen using AI vision."
                
        elif action == "click":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                return click_element_at(int(x), int(y), parameters.get("button", "left"), parameters.get("clicks", 1))
            else:
                pyautogui.click(button=parameters.get("button", "left"))
                return "Clicked at current mouse position"
                
        elif action == "type":
            text = parameters.get("text", "")
            clear_first = parameters.get("clear_first", False)
            return human_type(text, clear_first)
            
        elif action == "move":
            x = int(parameters.get("x", 0))
            y = int(parameters.get("y", 0))
            move_mouse_human_like(x, y)
            return f"Moved mouse to ({x}, {y})"
            
        elif action == "drag":
            x1 = int(parameters.get("x1", 0))
            y1 = int(parameters.get("y1", 0))
            x2 = int(parameters.get("x2", 0))
            y2 = int(parameters.get("y2", 0))
            move_mouse_human_like(x1, y1)
            time.sleep(0.1)
            pyautogui.dragTo(x2, y2, duration=0.5, button="left")
            return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})"
            
        elif action == "hotkey":
            keys = parameters.get("keys", "")
            if isinstance(keys, str):
                key_list = [k.strip() for k in keys.split("+")]
            else:
                key_list = keys
            pyautogui.hotkey(*key_list)
            return f"Executed hotkey: {keys}"
            
        elif action == "press":
            key = parameters.get("key", "enter")
            pyautogui.press(key)
            return f"Pressed key: {key}"
            
        elif action == "scroll":
            direction = parameters.get("direction", "down")
            amount = int(parameters.get("amount", 300))
            clicks = -amount if direction == "down" else amount
            pyautogui.scroll(clicks)
            return f"Scrolled {direction} by {amount}"
            
        elif action == "screenshot":
            path = parameters.get("path")
            if not path:
                path = str(Path.home() / "Desktop" / f"screenshot_{int(time.time())}.png")
            pyautogui.screenshot(path)
            return f"Saved screenshot to: {path}"
            
        else:
            return f"Unknown action: {action}"
            
    except Exception as e:
        print(f"[AI Desktop Control] Exception: {e}")
        return f"AI desktop control action failed: {e}"
