import asyncio
import re
import threading
import json
import sys
import traceback
import time
from threading import Event
from pathlib import Path
import io

# Fix Windows CP1252 encoding for emoji/unicode in print statements
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from core.action_verifier import verify_visible_action
from core.context_engine import get_context
from core import local_vision
from core.diagnostics import detailed_snapshot, format_snapshot
from core.visual_diff import capture_screen
from core.audio_devices import select_device, configured_preference
from core.command_normalizer import normalize_command
from core.performance import get_tracker
from core import offline_voice

from actions.file_processor import file_processor
from actions.image_generator import image_generator
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.automation        import automation
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.local_workflow    import local_workflow
from actions.game_updater      import game_updater
from actions.study_assistant   import study_assistant
from actions.live_translate    import live_translate
from actions.gesture_control   import gesture_control
from actions.predictive_action import predictive_action, log_interaction
from actions.system_control    import system_control
from agent.evolved_loader      import load_evolved_tools, reload_evolved
from agent.evolution_agent     import get_evolution_agent, evolution_agent_tool
from actions.image_generator   import image_generator

# AI Pro Upgrades
from actions.ai_desktop_control import ai_desktop_control
from actions.ai_browser import ai_browser
from actions.ai_screen_vision import ai_screen_vision
from actions.ai_image_gen import ai_image_generator
from actions.ai_audio import ai_audio_generator
from actions.ai_code_pro import ai_code_pro
from actions.ai_document import ai_document
from actions.ai_translate import ai_translate
from actions.ai_vision_chat import ai_vision_chat
from actions.ai_agent_tasks import ai_agent_tasks
from actions.ai_reasoning import ai_reasoning
from actions.ai_embeddings import ai_embeddings
from actions.ai_system_intel import ai_system_intel
from actions.ai_multi_chat import ai_multi_chat


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
# Keep live audio shallow so a busy connection never plays stale speech seconds late.
AUDIO_QUEUE_LIMIT   = 8
OUT_QUEUE_LIMIT     = 8
PLAYBACK_QUEUE_LIMIT = 64
MIC_GAIN            = 1.3
OUTPUT_BLOCK_SIZE   = 1024
# Vision is intentionally lazy: only explicit visual requests or failed
# deterministic UI lookups may load the local VLM.
VISION_MODE         = "on_demand"
AUDIO_IN_MIME_TYPE  = f"audio/pcm;rate={SEND_SAMPLE_RATE}"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Default to Hinglish in Roman script for this user unless English is requested. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _is_explicit_action_failure(result: object) -> bool:
    text = str(result or "").lower()
    return any(marker in text for marker in (
        "could not", "failed", "failure", "error:", "not found",
        "timed out", "timeout", "unable to", "no action",
    ))


def _needs_visual_verification(tool_name: str) -> bool:
    return tool_name in {
        "open_app", "browser_control", "computer_control", "computer_settings",
        "desktop_control", "ai_desktop_control", "ai_browser", "game_updater",
    }


def _is_click_like_action(tool_name: str, args: dict) -> bool:
    """Only verify actions whose visible result can be judged from the screen."""
    action = str((args or {}).get("action", "")).lower().strip()
    # Browser DOM/URL actions already have deterministic evidence and must stay
    # instant. Vision is reserved for coordinate-based or visually ambiguous
    # desktop operations where a text result alone is not trustworthy.
    if tool_name == "ai_browser":
        return action == "smart_click"
    if tool_name in {"computer_control", "ai_desktop_control"}:
        return action in {"screen_click", "smart_click", "screen_find_click"}
    return False

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Gives the weather report to user. Use normal mode for ordinary weather requests. "
            "Use map mode only when the user specifically asks for a weather map, radar, "
            "rain map, temperature map, wind map, cloud map, pressure map, or weather layers."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"},
                "time": {"type": "STRING", "description": "Optional time phrase for a normal weather report, e.g. today or tomorrow"},
                "mode": {"type": "STRING", "description": "normal or map. Set to map only for explicit weather map/radar/layer requests."},
                "map": {"type": "BOOLEAN", "description": "True only when the user explicitly requests Weather Map Mode."},
                "request": {"type": "STRING", "description": "Original weather request text when useful for detecting map/radar intent."}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "automation",
        "description": "Creates and manages named J.A.R.V.I.S routines. Creating one requires the owner's explicit approval because it can run later without a conversation.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "create | list | run | pause | resume | delete"},
                "id": {"type": "STRING", "description": "Automation id for run, pause, resume or delete"},
                "name": {"type": "STRING", "description": "Short unique name when creating"},
                "goal": {"type": "STRING", "description": "Exact task to execute when the routine fires"},
                "schedule": {"type": "STRING", "description": "once | daily | weekly"},
                "run_at": {"type": "STRING", "description": "YYYY-MM-DD HH:MM for a one-time routine"},
                "time": {"type": "STRING", "description": "HH:MM for daily or weekly routine"},
                "day": {"type": "STRING", "description": "Weekday for weekly routine"},
                "approved": {"type": "BOOLEAN", "description": "True only after the owner explicitly approves this exact routine"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "local_workflow",
        "description": "Creates and runs free local Windows automation workflows. It uses native UI Automation control labels and deterministic steps; no paid AI or cloud calls are used.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "create | list | run | delete"},
                "id": {"type": "STRING", "description": "Workflow id for run or delete"},
                "name": {"type": "STRING", "description": "Workflow name for create"},
                "steps": {"type": "ARRAY", "description": "Deterministic steps. Each has action: open_app, focus_window, click, type, press, hotkey, scroll, wait, or assert_visible; click/assert_visible use target labels."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "Use it automatically whenever a desktop/UI action requires seeing or locating something. "
            "You have NO visual ability without this tool. Return the result concisely."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Window management actions: list_windows (list all open windows), "
            "focus_window (bring a specific window to front by title), "
            "close_window_by_title (close a specific window by title), "
            "resize_window (resize active window to width x height), "
            "move_window (move active window to x,y position), "
            "snap_top, snap_bottom (snap window to screen edges). "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform. Window management: list_windows | focus_window | close_window_by_title | resize_window | move_window | snap_left | snap_right | snap_top | snap_bottom | minimize | maximize"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, window title for focus/close, etc."},
                "title":       {"type": "STRING", "description": "Window title for focus_window or close_window_by_title"},
                "width":       {"type": "INTEGER", "description": "Width in pixels for resize_window"},
                "height":      {"type": "INTEGER", "description": "Height in pixels for resize_window"},
                "x":           {"type": "INTEGER", "description": "X position for move_window"},
                "y":           {"type": "INTEGER", "description": "Y position for move_window"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "IMPORTANT: For navigating WITHIN an already-open page (e.g. clicking 'Fashion' on Flipkart, "
            "'Electronics' on Amazon, a menu item on any site) — use action='in_page_navigate' with target=<item name>. "
            "This is FASTER than smart_click and avoids Google search. "
            "If a query names Flipkart, Amazon, YouTube, or another site, search INSIDE that site; never redirect it to Google. "
            "For multi-step result tasks, keep looping through search, state verification, scrolling, reading, and explanation until the requested scope is complete. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously. "
            "DO NOT use screen_process to verify browser actions — use get_url or get_state instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | upload_file | scroll | fill_form | smart_click | smart_type | in_page_navigate | get_text | get_url | get_state | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query only. Use the exact product/topic words, not instruction text like analyze results or batao."},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "path":        {"type": "STRING", "description": "Absolute local file path for upload_file"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "target":      {"type": "STRING", "description": "Nav item / category / link name for in_page_navigate (e.g. 'Fashion', 'Electronics', 'Login')"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },

    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Multi-step tasks: research + save file, organize files, mixed tools. "
            "For browser work, prefer direct browser_control steps. "
            "NEVER for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "study_assistant",
        "description": (
            "AI-driven personalized study assistant. "
            "Tracks study sessions and progress, generates customized weekly study plans, "
            "suggests topics and free learning resources, analyzes weak/strong subjects, "
            "and creates quizzes. Use whenever user wants to study, track learning, get a study plan, "
            "or practice with a quiz."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING",  "description": "log | plan | suggest | analyze | quiz"},
                "subject":      {"type": "STRING",  "description": "Subject name e.g. Math, Physics, English"},
                "topic":        {"type": "STRING",  "description": "Specific topic within the subject"},
                "goal":         {"type": "STRING",  "description": "Study goal e.g. pass exam, improve grade"},
                "duration_min": {"type": "INTEGER", "description": "Study session duration in minutes (for log)"},
                "score":        {"type": "INTEGER", "description": "Test/quiz score out of 100 (for log)"},
                "notes":        {"type": "STRING",  "description": "Any notes about the session (for log)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "live_translate",
        "description": (
            "Real-time language translation and transcription. "
            "Can translate any text to any language, transcribe audio/video files to text, "
            "record mic and transcribe speech, or record mic and translate speech live. "
            "Useful for meetings, classes, lectures, and multilingual communication."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "translate | transcribe | transcribe_translate | record_transcribe | record_translate"},
                "text":        {"type": "STRING", "description": "Text to translate (for translate action)"},
                "target_lang": {"type": "STRING", "description": "Target language e.g. Hindi, Turkish, French (default: English)"},
                "source_lang": {"type": "STRING", "description": "Source language, or 'auto' to detect (default: auto)"},
                "file_path":   {"type": "STRING", "description": "Path to audio/video file for transcribe actions"},
                "duration":    {"type": "INTEGER","description": "Mic recording duration in seconds (default: 10)"},
                "save":        {"type": "BOOLEAN","description": "Save result to Desktop as text file"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "system_control",
        "description": (
            "Deep system-level control for Windows. Three categories: "
            "(1) Window Manipulation: get info about any window, set window transparency/opacity, "
            "set always-on-top, tile windows. "
            "(2) Resource Management: real-time CPU/RAM/disk stats, list running processes, "
            "kill a process by name or PID, set process priority, disk cleanup, empty recycle bin. "
            "(3) Security Settings: firewall status/enable/disable, UAC status, "
            "Windows Defender status and scan, list/disable startup programs, check Windows updates. "
            "Use this for any hardware, security, or deep system request."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "Window: get_window_info | set_transparency | set_opacity | set_always_on_top | tile_windows. "
                        "Resource: resource_usage | list_processes | kill_process | set_priority | disk_cleanup | empty_recycle_bin. "
                        "Security: firewall_status | firewall_enable | firewall_disable | uac_status | "
                        "defender_status | defender_scan | list_startup | disable_startup | check_updates"
                    )
                },
                "title":     {"type": "STRING",  "description": "Window title (partial match) for window actions"},
                "alpha":     {"type": "INTEGER", "description": "Transparency alpha 0-255 for set_transparency"},
                "percent":   {"type": "INTEGER", "description": "Opacity percent 0-100 for set_opacity"},
                "enable":    {"type": "BOOLEAN", "description": "True/False for set_always_on_top or firewall toggle"},
                "direction": {"type": "STRING",  "description": "horizontal | vertical for tile_windows"},
                "top":       {"type": "INTEGER", "description": "Number of top processes to list (default 15)"},
                "name":      {"type": "STRING",  "description": "Process name for kill_process / set_priority / disable_startup"},
                "pid":       {"type": "STRING",  "description": "Process PID for kill_process / set_priority"},
                "priority":  {"type": "STRING",  "description": "low | below_normal | normal | above_normal | high | realtime"},
                "profile":   {"type": "STRING",  "description": "Firewall profile: domain | private | public | all"},
                "scan_type": {"type": "STRING",  "description": "quick | full for defender_scan"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "gesture_control",
        "description": (
            "Controls the computer using physical hand gestures via webcam. "
            "Start gesture mode so user can control volume, scroll, switch windows, "
            "take screenshots, minimize/maximize windows using hand gestures. "
            "Use when user asks to control computer with hands or gestures."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | status | map"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "predictive_action",
        "description": (
            "AI-powered next-action predictor. Analyzes the user's behavior patterns, "
            "conversation history, and memory to predict and optionally auto-execute "
            "the user's most likely next action. "
            "Use when user asks Jarvis to predict, anticipate, or proactively help."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "predict | confirm | log | clear"},
                "context":    {"type": "STRING", "description": "What the user just did or said, for better prediction"},
                "tool":       {"type": "STRING", "description": "Tool to confirm-execute (for confirm action)"},
                "parameters": {"type": "OBJECT", "description": "Parameters for confirm action"},
                "confirm":    {"type": "BOOLEAN","description": "True if user confirmed auto-execution"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "image_generator",
        "description": (
            "Generates high-quality AI images using Hugging Face FLUX.1. "
            "Use when user asks to create, draw, generate, or make any image, photo, artwork, wallpaper, or illustration. "
            "Always call this tool — never just describe the image."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt":  {"type": "STRING", "description": "Detailed description of what to generate"},
                "style":   {"type": "STRING", "description": "Art style e.g. realistic, anime, oil painting, cyberpunk, watercolor"},
                "width":   {"type": "INTEGER", "description": "Image width in pixels (default: 1024)"},
                "height":  {"type": "INTEGER", "description": "Image height in pixels (default: 1024)"},
                "save_to": {"type": "STRING", "description": "Custom save path (optional, default: Desktop)"},
            },
            "required": ["prompt"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "system_health",
        "description": (
            "Run a fast local health check for API configuration, local vision, browser, "
            "desktop control, microphone, and current audio devices. Use when the user asks "
            "whether JARVIS is working, why it is slow/silent, or asks for diagnostics."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "evolution_agent",
        "description": (
            "JARVIS ka internal self-improvement agent. "
            "Use when: koi tool baar-baar fail ho, user naya capability chahe, "
            "ya evolution status puchhe. Actions: status | run_now | suggest | fix_failure. "
            "Ye agent background me roz ek naya evolved tool bhi add karta hai."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "status | run_now | suggest | fix_failure"},
                "context":   {"type": "STRING", "description": "Extra context for suggest or fix"},
                "tool_name": {"type": "STRING", "description": "Failed tool name for fix_failure"},
            },
            "required": []
        }
    },
    {
        "name": "ai_desktop_control",
        "description": "Human-like desktop control using Bezier curves and AI vision. Use for click, type, drag, scroll, hotkeys, screen finding and precision clicks.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "click | type | move | drag | hotkey | press | scroll | screenshot | screen_find_click"},
                "text": {"type": "STRING", "description": "Text to type"},
                "description": {"type": "STRING", "description": "Visual description of element to click/find (for screen_find_click)"},
                "x": {"type": "INTEGER", "description": "X coordinate"},
                "y": {"type": "INTEGER", "description": "Y coordinate"},
                "button": {"type": "STRING", "description": "left | right (default left)"},
                "keys": {"type": "STRING", "description": "Hotkey combo (e.g. 'ctrl+c')"},
                "key": {"type": "STRING", "description": "Single key (e.g. 'enter')"},
                "direction": {"type": "STRING", "description": "up | down"},
                "amount": {"type": "INTEGER", "description": "Scroll amount (default 300)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_browser",
        "description": "Smart AI browser control. Use for intelligent navigation, AI vision clicks, form filling, text extraction, scraping, and tab cleanup.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "smart_navigate | smart_click | smart_type | scrape_page | tab_cleanup"},
                "url": {"type": "STRING", "description": "Target website URL"},
                "query": {"type": "STRING", "description": "Search query"},
                "description": {"type": "STRING", "description": "Visual description of button or link to click or input to type in"},
                "text": {"type": "STRING", "description": "Text to type into input"},
                "browser": {"type": "STRING", "description": "chrome | edge | firefox | operagx etc."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_screen_vision",
        "description": "Advanced screen and camera vision utility. Supports screen/webcam analysis, OCR, and background screen monitoring.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "analyze_screen | analyze_camera | ocr | start_monitor | stop_monitor"},
                "text": {"type": "STRING", "description": "Question or instructions about the screen/camera view"},
                "target_text": {"type": "STRING", "description": "Text to watch for in monitoring mode"},
                "interval": {"type": "INTEGER", "description": "Seconds between checks (default 5)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_image_gen",
        "description": "AI image generation using HF models. Support different qualities, resolutions, and art styles.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Image description"},
                "style": {"type": "STRING", "description": "Art style (e.g. anime, realistic, cyberpunk)"},
                "model_type": {"type": "STRING", "description": "fast | quality | classic (default fast)"},
                "width": {"type": "INTEGER", "description": "Image width (default 1024)"},
                "height": {"type": "INTEGER", "description": "Image height (default 1024)"},
                "save_path": {"type": "STRING", "description": "Output path"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "ai_audio",
        "description": "Advanced voice and music utility. Supports music generation, voice speech generation (TTS), and audio transcription (STT).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "generate_music | transcribe_audio | text_to_speech"},
                "prompt": {"type": "STRING", "description": "Music description or text to convert to speech"},
                "file_path": {"type": "STRING", "description": "Input audio file to transcribe"},
                "target_path": {"type": "STRING", "description": "Output audio file path"},
                "voice": {"type": "STRING", "description": "Voice style: suno/bark | coqui/XTTS-v2"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_code_pro",
        "description": "Pro-level coding agent. Generates, reviews, refactors complex codes, and writes SQL queries.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "generate | review | sql_gen | project_refactor"},
                "description": {"type": "STRING", "description": "Coding request or query definition"},
                "language": {"type": "STRING", "description": "Programming language (default python)"},
                "file_path": {"type": "STRING", "description": "Code file path to review or refactor"},
                "schema": {"type": "STRING", "description": "Database schema description for SQL generation"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_document",
        "description": "Document intelligence and Q&A. Supports parsing and analyzing long PDFs, Word documents, or text files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "summarize | qa | generate_report | extract_entities"},
                "file_path": {"type": "STRING", "description": "Path to PDF, Word or text file"},
                "question": {"type": "STRING", "description": "Question to ask about the document"},
                "requirements": {"type": "STRING", "description": "Formatting or design guidelines for reports"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_translate",
        "description": "Real-time AI translation. Supports translation of text, documents, and real-time screen content.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "translate_text | translate_screen | translate_document"},
                "text": {"type": "STRING", "description": "Text to translate"},
                "target_lang": {"type": "STRING", "description": "Target language (default English)"},
                "file_path": {"type": "STRING", "description": "Document to translate"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_vision_chat",
        "description": "Visual chat assistant. Allows chatting about images, object detection, and generating descriptions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "chat | detect_objects | caption"},
                "image_path": {"type": "STRING", "description": "Path to target image"},
                "question": {"type": "STRING", "description": "Question to ask about image"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_agent_tasks",
        "description": "Complex workflow planner and executor. Plans multi-step flows and conditional automation triggers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "plan_workflow | run_workflow | conditional_alert"},
                "goal": {"type": "STRING", "description": "Goal or description of what to accomplish"},
                "workflow_name": {"type": "STRING", "description": "Optional workflow preset"},
                "condition": {"type": "STRING", "description": "Trigger condition for monitoring"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_reasoning",
        "description": "Deep reasoning, step-by-step math solver, and technical research compiler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "solve_math | logical_reasoning | deep_research"},
                "query": {"type": "STRING", "description": "Math problem, logic puzzle, or research topic"},
                "steps": {"type": "BOOLEAN", "description": "Include step-by-step reasoning details"}
            },
            "required": ["action", "query"]
        }
    },
    {
        "name": "ai_embeddings",
        "description": "Semantic search tool. Search files and folders by meaning rather than simple keyword matches.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "search_files | search_notes"},
                "query": {"type": "STRING", "description": "Concept, topic, or description to search for"},
                "path": {"type": "STRING", "description": "Root path to search in"},
                "extension": {"type": "STRING", "description": "File extension to filter"}
            },
            "required": ["action", "query"]
        }
    },
    {
        "name": "ai_system_intel",
        "description": "AI-powered system scanner. Checks diagnostic health, cleans temporary files, and optimizes storage usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "diagnose | list_heavy_processes | storage_advice | clean_temp"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ai_multi_chat",
        "description": "AI model switching and response comparisons. Choose or switch models in conversations.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "switch_model | compare_models | ask_model | get_active_model"},
                "model_name": {"type": "STRING", "description": "Target model alias (e.g. deepseek, qwen, llama)"},
                "query": {"type": "STRING", "description": "Prompt or query text"}
            },
            "required": ["action"]
        }
    }
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._mic_status_warned = False
        self.ui.on_text_command = self._on_text_command
        self.ui.on_cancel_task = self.cancel_active_task
        self._turn_done_event: asyncio.Event | None = None
        self._cancel_requested = Event()
        self.context = get_context()
        self._reload_evolved_tools()

    def _reload_evolved_tools(self):
        self._evolved_decls, self._evolved_handlers = reload_evolved()

    def _all_tool_declarations(self) -> list:
        return TOOL_DECLARATIONS + self._evolved_decls

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            self.ui.update_task("RECOVERY", "Live connection is not ready")
            self.ui.write_log("ERR: Command rejected — live session unavailable")
            return
        clean_text = normalize_command(text)
        self.context.user_command(clean_text)
        self._cancel_requested.clear()
        self.ui.update_task("RECEIVED", "Parsing your command…")
        if clean_text != str(text).strip():
            self.ui.write_log(f"CMD: normalized → {clean_text}")
        future = asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": clean_text}]},
                turn_complete=True
            ),
            self._loop
        )
        future.add_done_callback(self._report_send_error)

    def _report_send_error(self, future):
        try:
            error = future.exception()
        except Exception as exc:
            error = exc
        if error:
            self.ui.update_task("RECOVERY", "Live connection error — reconnecting")
            self.ui.write_log(f"ERR: Live command send failed — {str(error)[:180]}")

    def cancel_active_task(self):
        self._cancel_requested.set()
        self.ui.update_task("RECOVERY", "Cancellation requested")
        self.ui.write_log("SYS: Active task cancellation requested.")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            # Keep short system/error responses audible during reconnects.
            threading.Thread(target=offline_voice.speak, args=(text,), daemon=True,
                             name="OfflineTTS").start()
            return
        future = asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )
        future.add_done_callback(self._report_send_error)

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} me error aa gaya. {short}")

    def _background_task_update(self, task_id: str, status: str):
        """Reflect queued agent work in the live HUD without blocking it."""
        self.ui.update_task("EXECUTING", f"Background task {task_id}: {status}")
        self.ui.write_log(f"TASK {task_id}: {status.upper()}")

    def _background_task_complete(self, task_id: str, result, error=None):
        if error:
            message = f"Background task {task_id} failed: {str(error)[:180]}"
            self.ui.update_task("RECOVERY", message)
            self.ui.write_log(f"ERR: {message}")
            self.speak(f"Sir, background task {task_id} failed. {str(error)[:120]}")
            return
        message = f"Background task {task_id} completed."
        self.ui.update_task("READY", message)
        self.ui.write_log(f"TASK {task_id}: COMPLETED")
        self.speak(message)

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )
        audio_ctx = (
            "[LIVE AUDIO]\n"
            "You are connected to the user's live microphone audio through Gemini Live. "
            "Treat spoken input as real user speech. Never say you are text-based or that "
            "you cannot hear audio. If speech is unclear, ask the user to repeat briefly.\n\n"
        )

        parts = [time_ctx, audio_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(
            "[LIVE SESSION CONTEXT]\n" + self.context.prompt_context() + "\n"
            "Use this only to resolve references and continue unfinished goals; "
            "never claim a previous action succeeded without its evidence."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            # High sensitivity catches quiet speech; a short but non-zero
            # silence window commits the turn quickly after the sentence ends.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=180,
                    silence_duration_ms=450,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=0,
            ),
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": self._all_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        if self._cancel_requested.is_set():
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ACTION_CANCELLED — User cancelled the active task."},
            )

        print(f"[JARVIS] 🔧 {name}  {args}")
        tool_started_at = time.perf_counter()
        self.ui.set_state("THINKING")
        self.context.tool_started(name, args)
        self.ui.update_task("EXECUTING", f"{name} · {args.get('action', 'running')}")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        if name == "system_health":
            health = format_snapshot(detailed_snapshot(BASE_DIR))
            perf = get_tracker().snapshot().get("tools", {})
            if perf:
                slowest = sorted(perf.items(), key=lambda item: item[1].get("avg_ms", 0), reverse=True)[:3]
                health += "\n\nPerformance:\n" + "\n".join(
                    f"{tool}: {stats['calls']} calls, avg {stats['avg_ms']}ms, failed {stats['failed']}"
                    for tool, stats in slowest
                )
            self.ui.write_log("HEALTH CHECK:\n" + health)
            self.ui.update_task("READY", "Health check complete")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": health}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."
        visual_before = None
        if _needs_visual_verification(name) and _is_click_like_action(name, args):
            try:
                visual_before = await loop.run_in_executor(None, capture_screen)
            except Exception as capture_error:
                self.ui.write_log(f"VERIFY: pre-action screenshot unavailable — {capture_error}")

        try:
            if name in getattr(self, "_evolved_handlers", {}):
                handler = self._evolved_handlers[name]
                r = await loop.run_in_executor(
                    None,
                    lambda: handler(parameters=args, player=self.ui, speak=self.speak),
                )
                result = r or "Done."

            elif name == "evolution_agent":
                r = await loop.run_in_executor(
                    None,
                    lambda: evolution_agent_tool(parameters=args, player=self.ui, speak=self.speak),
                )
                result = r or "Done."

            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "automation":
                r = await loop.run_in_executor(None, lambda: automation(parameters=args, player=self.ui))
                result = r or "Automation updated."

            elif name == "local_workflow":
                r = await loop.run_in_executor(None, lambda: local_workflow(parameters=args, player=self.ui))
                result = r or "Local workflow finished."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                r = await loop.run_in_executor(
                    None,
                    lambda: screen_process(parameters=args, response=None,
                                            player=self.ui, session_memory=None),
                )
                result = r or "Screen analysis could not be completed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(
                    goal=args.get("goal", ""), priority=priority, speak=self.speak,
                    on_update=self._background_task_update,
                    on_complete=self._background_task_complete,
                )
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "image_generator":
                r = await loop.run_in_executor(
                    None,
                    lambda: image_generator(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "study_assistant":
                r = await loop.run_in_executor(None, lambda: study_assistant(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "live_translate":
                r = await loop.run_in_executor(None, lambda: live_translate(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "system_control":
                r = await loop.run_in_executor(None, lambda: system_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "gesture_control":
                r = await loop.run_in_executor(None, lambda: gesture_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_desktop_control":
                r = await loop.run_in_executor(None, lambda: ai_desktop_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_browser":
                r = await loop.run_in_executor(None, lambda: ai_browser(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_screen_vision":
                r = await loop.run_in_executor(None, lambda: ai_screen_vision(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_image_gen":
                r = await loop.run_in_executor(None, lambda: ai_image_generator(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_audio":
                r = await loop.run_in_executor(None, lambda: ai_audio_generator(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_code_pro":
                r = await loop.run_in_executor(None, lambda: ai_code_pro(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_document":
                r = await loop.run_in_executor(None, lambda: ai_document(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_translate":
                r = await loop.run_in_executor(None, lambda: ai_translate(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_vision_chat":
                r = await loop.run_in_executor(None, lambda: ai_vision_chat(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_agent_tasks":
                r = await loop.run_in_executor(None, lambda: ai_agent_tasks(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_reasoning":
                r = await loop.run_in_executor(None, lambda: ai_reasoning(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_embeddings":
                r = await loop.run_in_executor(None, lambda: ai_embeddings(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_system_intel":
                r = await loop.run_in_executor(None, lambda: ai_system_intel(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "ai_multi_chat":
                r = await loop.run_in_executor(None, lambda: ai_multi_chat(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "predictive_action":
                r = await loop.run_in_executor(None, lambda: predictive_action(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Theek hai sir, goodbye.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)
            try:
                get_evolution_agent().report_failure(
                    name, str(e), str(dict(fc.args or {}))[:200]
                )
            except Exception:
                pass

        # A tool response is only an attempt. For visible click actions, ask a
        # strict visual verifier to inspect the actual screen before allowing
        # the live model to claim success. This prevents false "done" replies
        # when a coordinate was wrong, a window was covered, or a click was
        # swallowed by the UI.
        if (
            _needs_visual_verification(name)
            and _is_click_like_action(name, args)
            and not _is_explicit_action_failure(result)
        ):
            try:
                self.ui.update_task("VERIFYING", f"Checking {name} result…")
                verification, evidence = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: verify_visible_action(
                            f"{name} {json.dumps(args, ensure_ascii=True)}", str(result), visual_before
                        ),
                    ),
                    timeout=4.0,
                )
                if verification == "FAIL":
                    result = f"ACTION_FAILED — Visual verification failed: {evidence}"
                    self.ui.write_log(f"VERIFY: FAIL — {evidence}")
                elif verification == "UNCERTAIN":
                    result = f"ACTION_UNCERTAIN — Visual verification inconclusive: {evidence}"
                    self.ui.write_log(f"VERIFY: UNCERTAIN — {evidence}")
                else:
                    result = f"{result} | VERIFIED: {evidence}"
                    self.ui.write_log(f"VERIFY: PASS — {evidence}")
            except asyncio.TimeoutError:
                result = "ACTION_UNCERTAIN — Visual verification timed out; do not claim success."
                self.ui.write_log("VERIFY: UNCERTAIN — verifier timeout")
            except Exception as verify_err:
                result = f"ACTION_UNCERTAIN — Visual verification unavailable: {verify_err}"
                self.ui.write_log(f"VERIFY: UNCERTAIN — {verify_err}")

        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.context.tool_finished(result)
        if _is_explicit_action_failure(result):
            self.ui.update_task("RECOVERY", "Action needs another approach")
        else:
            self.ui.update_task("READY", "Verified response ready")

        if _is_explicit_action_failure(result):
            # Make failures unambiguous to the live model so it retries or
            # reports the problem instead of confidently saying "done".
            result = f"ACTION_FAILED — {result}"
        elapsed_ms = (time.perf_counter() - tool_started_at) * 1000.0
        get_tracker().record(name, elapsed_ms, _is_explicit_action_failure(result))
        if elapsed_ms >= 1500:
            self.ui.write_log(f"PERF: {name} took {elapsed_ms / 1000:.2f}s")
        # Do not put a 2–3 second local-VLM call in the live response path.
        # Browser DOM/URL and tool-specific checks are immediate; local vision
        # remains an on-demand fallback inside browser/desktop actions.

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        if name not in ("save_memory", "shutdown_jarvis", "predictive_action"):
            threading.Thread(
                target=log_interaction,
                args=(str(dict(fc.args or {}))[:80], name, str(result)[:60]),
                daemon=True
            ).start()

            return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            # New GenAI SDK / Gemini 3.x expects PCM input under `audio`.
            # Passing it as `media` serializes the deprecated media_chunks field.
            await self.session.send_realtime_input(audio=msg)

    def _queue_audio_chunk(self, msg: dict) -> None:
        if not self.out_queue:
            return
        try:
            self.out_queue.put_nowait(msg)
            return
        except asyncio.QueueFull:
            try:
                self.out_queue.get_nowait()
            except Exception:
                pass
        try:
            self.out_queue.put_nowait(msg)
        except Exception:
            pass

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if status and not self._mic_status_warned:
                self._mic_status_warned = True
                msg = f"Mic warning: {status}"
                print(f"[JARVIS] ⚠️ {msg}")
                try:
                    self.ui.write_log(f"SYS: {msg}")
                except Exception:
                    pass
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted: # Mic active
                # Gentle software gain improves quiet-mic recognition without
                # changing the sounddevice input device or OS mixer settings.
                samples = indata.astype(np.int32)
                samples = np.clip(samples * MIC_GAIN, -32768, 32767).astype(np.int16)
                data = samples.tobytes()
                loop.call_soon_threadsafe(
                    self._queue_audio_chunk,
                    {"data": data, "mime_type": AUDIO_IN_MIME_TYPE}
                )

        try:
            try:
                device_info = sd.query_devices(kind="input")
                name = str(device_info.get("name", "default microphone"))
                self.ui.write_log(f"SYS: Microphone connected: {name}")
            except Exception:
                pass
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=select_device("input", configured_preference(BASE_DIR, "input")),
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            try:
                self.ui.write_log(f"SYS: Microphone error: {e}")
            except Exception:
                pass
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        # Never drop generated audio chunks: dropping them
                        # causes audible skips, speed-ups, and repetitions.
                        await self.audio_in_queue.put(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        output_device = select_device(
            "output", configured_preference(BASE_DIR, "output")
        )
        try:
            if output_device is not None:
                self.ui.write_log(f"SYS: Audio output device: {sd.query_devices(output_device)['name']}")
        except Exception:
            pass
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=OUTPUT_BLOCK_SIZE,
            device=output_device,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        reconnect_delay = 1.0
        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue(maxsize=PLAYBACK_QUEUE_LIMIT)
                    self.out_queue      = asyncio.Queue(maxsize=OUT_QUEUE_LIMIT)
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    reconnect_delay = 1.0
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                # Keep the console usable during quota/network outages.
                if "quota" not in str(e).lower() and "media_chunks" not in str(e).lower():
                    traceback.print_exc()
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print(f"[JARVIS] 🔄 Reconnecting in {reconnect_delay:.0f}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2.0, 30.0)

def main():
    ui = JarvisUI("face.png")

    def runner():
        try:
            ui.write_log("SYS: Startup — Gemini 2.5 mode")
            print("[JARVIS] Startup: waiting for configuration")
            ui.wait_for_api_key()
            print("[JARVIS] Startup: configuration ready")

            def _on_evolved_updated():
                if getattr(ui, "_jarvis", None):
                    ui._jarvis._reload_evolved_tools()

            evo = get_evolution_agent(
                on_tools_updated=_on_evolved_updated,
                ui_log=ui.write_log,
            )
            evo.start()
            print("[JARVIS] Startup: background services ready")

            # Warm local vision in the background so the first screenshot/OCR
            # request does not pay the model-load penalty on the live path.
            def _warm_vision():
                ok = local_vision.warmup()
                ui.write_log("SYS: Local vision ready." if ok else "SYS: Local vision fallback available.")
            threading.Thread(target=_warm_vision, daemon=True, name="VisionWarmup").start()

            jarvis = JarvisLive(ui)
            ui._jarvis = jarvis
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        except Exception as exc:
            # The runner is a daemon thread; without this, startup exceptions
            # silently leave the UI open while Jarvis itself is offline.
            print(f"[JARVIS] Runner stopped: {exc}")
            traceback.print_exc()
            ui.write_log(f"ERR: startup — {exc}")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
