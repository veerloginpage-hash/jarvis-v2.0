import json
import re
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


PLANNER_PROMPT = """You are the planning module of VEER INDUS, a personal AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- NEVER reference previous step results in parameters. Every step is independent.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Max 8 steps. Use the minimum steps needed, but never omit a required verify/read/finalize step.

CREATIVE PROBLEM-SOLVING RULES:
- If the obvious tool for a task does not exist in the list, find an alternative combination of available tools that achieves the same outcome.
- Decompose complex goals into the smallest possible independent steps, each using one tool.
- Never produce a plan that says a goal is impossible — always find a creative path using available tools.
- If a goal requires information AND an action, always search first, then act using the retrieved data.
- Synthesize: when multiple search results are available, combine them into a single richer output step.

ADVANCED CONTEXTUAL UNDERSTANDING RULES:
- Read the goal carefully for pronouns or references ("it", "that file", "the same one") and resolve them before planning.
- If the goal is a follow-up to a previous task (e.g. "now save it", "edit that"), treat the implied subject as a parameter and fill it in logically.
- Choose tool parameters that match the inferred full intent, not just the literal words.

AVAILABLE TOOLS AND THEIR PARAMETERS:

system_health
  no parameters — inspect local API, vision, browser, desktop, microphone, and audio readiness

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "click" | "type" | "upload_file" | "scroll" | "fill_form" | "smart_click" | "smart_type" | "in_page_navigate" | "get_text" | "get_url" | "get_state" | "press" | "new_tab" | "close_tab" | "close" (required)
  url: string (for go_to)
  query: string (for search)
  text: string (for click/type)
  path: string (for upload_file)
  direction: "up" | "down" (for scroll)
  description/target: natural-language element or navigation target (for smart_click/in_page_navigate)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

computer_settings
  action: string (required)
  description: string — natural language description
  value: string (optional)

computer_control
  action: "type" | "smart_type" | "click" | "double_click" | "right_click" | "hotkey" | "press" | "scroll" | "screenshot" | "wait" | "focus_window" | "screen_find" | "screen_click" | "smart_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

send_message
  receiver: string (required)
  message_text: string (required)
  platform: string (required)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

automation
  action: "create" | "list" | "run" | "pause" | "resume" | "delete" (required)
  id: string (required for run/pause/resume/delete)
  name: string (required for create)
  goal: string (required for create) — the exact approved routine goal
  schedule: "once" | "daily" | "weekly" (required for create)
  run_at: string YYYY-MM-DD HH:MM (required when schedule is once)
  time: string HH:MM (required when daily/weekly)
  day: weekday string (required when weekly)
  approved: boolean — only true after the user explicitly confirms the unattended routine

desktop_control
  action: "wallpaper" | "organize" | "clean" | "list" | "task" (required)
  path: string (optional)
  task: string (optional)

youtube_video
  action: "play" | "summarize" | "trending" (required)
  query: string (for play)

weather_report
  city: string (required)
  time: string (optional)
  mode: "normal" | "map" (optional) — use "map" only when the user explicitly asks for a weather map, radar, rain map, temperature map, wind map, cloud map, pressure map, or weather layers
  map: boolean (optional) — true only for explicit Weather Map Mode requests

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)

code_helper
  action: "write" | "edit" | "run" | "explain" (required)
  description: string (required)
  language: string (optional)
  output_path: string (optional)
  file_path: string (optional)

dev_agent
  description: string (required)
  language: string (optional)

EXAMPLES:

Goal: "research mechanical engineering and save it to a notepad file"
Steps:

web_search | query: "mechanical engineering overview definition history"
web_search | query: "mechanical engineering applications and future trends"
file_controller | action: write, path: desktop, name: mechanical_engineering.txt, content: "MECHANICAL ENGINEERING RESEARCH\n\nThis file will be filled with web research results."

Goal: "What is the price of Bitcoin"
Steps:

web_search | query: "Bitcoin price today USD"

Goal: "List the files on the desktop and find the largest 5 files"
Steps:

file_controller | action: list, path: desktop
file_controller | action: largest, path: desktop, count: 5

Goal: "Install PUBG from Steam"
Steps:

game_updater | action: install, platform: steam, game_name: "PUBG"

Goal: "Update all my Steam games"
Steps:

game_updater | action: update, platform: steam

Goal: "Send John a message on WhatsApp saying there is a meeting tomorrow"
Steps:

send_message | receiver: John, message_text: "There is a meeting tomorrow", platform: WhatsApp

Goal: "Open the clock and set a reminder for 30 minutes later"
Steps:

reminder | date: [today], time: [now+30min], message: "Reminder"

IMPORTANT FOR AUTOMATIONS: Never create a scheduled automation until the user
has explicitly reviewed and approved its exact goal and schedule. If that
approval is absent, ask for it in conversation instead of calling automation.

local_workflow
  action: "create" | "list" | "run" | "delete" (required)
  id: string (required for run/delete)
  name: string (required for create)
  steps: list of deterministic native UI Automation steps (required for create)
  Supported steps: open_app, focus_window, click, type, press, hotkey, scroll,
  wait, assert_visible. Click and assert_visible must use a visible UI label target.
  Use this for repeatable local workflows only; it never needs a paid cloud model.

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def create_plan(goal: str, context: str = "") -> dict:
    # Common one-action voice commands do not need a planner network round-trip.
    fast = _fast_plan(goal)
    if fast:
        print(f"[Planner] ⚡ Fast path: {fast['steps'][0]['tool']}")
        return fast

    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=PLANNER_PROMPT
    )

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        response = model.generate_content(user_input)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fast_plan(goal: str) -> dict | None:
    text = re.sub(r"\s+", " ", str(goal or "")).strip()
    lower = text.lower()
    if not text:
        return None

    # Chain only deterministic commands locally. Complex natural-language
    # requests still go through the full planner, but common sequences avoid
    # an unnecessary planning round-trip.
    parts = re.split(r"\s+(?:and\s+then|then|aur\s+phir|phir)\s+", text, flags=re.I)
    if len(parts) > 1:
        chained = []
        for part in parts:
            subplan = _fast_plan(part.strip())
            if not subplan:
                chained = []
                break
            chained.extend(subplan.get("steps", []))
        if chained:
            for index, step in enumerate(chained, 1):
                step["step"] = index
            return {"goal": goal, "steps": chained}

    if re.fullmatch(r"(?:status|health|health check|system status|is jarvis working|jarvis check)", lower):
        return {"goal": goal, "steps": [{"step": 1, "tool": "system_health", "description": "Check JARVIS system health", "parameters": {}, "critical": True}]}

    # URL navigation is deterministic and should never spend a model round-trip.
    url_match = re.match(r"(?:please\s+)?(?:open|go\s+to|visit|khol(?:o)?)\s+(https?://\S+|www\.\S+)$", text, re.I)
    if url_match:
        url = url_match.group(1)
        if url.startswith("www."):
            url = "https://" + url
        return {"goal": goal, "steps": [{"step": 1, "tool": "browser_control", "description": "Open website", "parameters": {"action": "go_to", "url": url}, "critical": True}]}

    match = re.match(r"(?:please\s+)?(?:open|launch|start|khol(?:o)?|chalao)\s+(.+)$", text, re.I)
    if match:
        return {"goal": goal, "steps": [{"step": 1, "tool": "open_app", "description": "Open application", "parameters": {"app_name": match.group(1).strip()}, "critical": True}]}

    match = re.match(r"(?:please\s+)?(?:search|find|dhund(?:ho)?|google)\s+(?:for\s+)?(.+)$", text, re.I)
    if match:
        return {"goal": goal, "steps": [{"step": 1, "tool": "web_search", "description": "Search the requested topic", "parameters": {"query": match.group(1).strip()}, "critical": True}]}

    if "youtube" in lower:
        match = re.match(r"(?:play|bajao)\s+(.+?)(?:\s+on\s+youtube|\s+youtube\s+par)?$", text, re.I)
        if match:
            return {"goal": goal, "steps": [{"step": 1, "tool": "youtube_video", "description": "Play requested video", "parameters": {"action": "play", "query": match.group(1).strip()}, "critical": True}]}

    match = re.match(r"(?:please\s+)?(?:navigate|go)\s+(?:to|on)\s+(.+)$", text, re.I)
    if match:
        target = match.group(1).strip()
        return {"goal": goal, "steps": [{"step": 1, "tool": "browser_control", "description": "Navigate browser", "parameters": {"action": "in_page_navigate", "target": target, "description": target}, "critical": True}]}

    match = re.match(r"(?:weather|mausam)(?:\s+(?:in|of|ka|ki|ke))?\s+(.+)$", text, re.I)
    if match:
        return {"goal": goal, "steps": [{"step": 1, "tool": "weather_report", "description": "Get current weather", "parameters": {"city": match.group(1).strip()}, "critical": True}]}
    return None


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": _clean_goal_search_query(goal)},
                "critical": True
            }
        ]
    }


def _browser_fallback_params(goal: str) -> dict:
    g = _clean_goal_search_query(goal)
    lower = g.lower()
    if any(site in lower for site in ("youtube", "google", "amazon", "flipkart", "instagram", "facebook")):
        return {"action": "search", "query": g}
    if "." in g and " " not in g:
        return {"action": "go_to", "url": g}
    return {"action": "search", "query": g}


def _clean_goal_search_query(goal: str) -> str:
    text = re.sub(r"\s+", " ", str(goal or "")).strip(" ,.-")
    quoted = re.findall(r"[\"'“”‘’](.+?)[\"'“”‘’]", text)
    if quoted:
        return quoted[0].strip()

    patterns = [
        r"(?:flipkart|amazon|google|youtube)\s+(?:par|pe|mein|me|on)?\s*(.+?)\s+(?:search|dhund|find)\b",
        r"(.+?)\s+(?:search|dhund|find)\s+(?:karo|karein|karna|maar|marna)\b",
        r"(?:search\s+(?:for\s+)?|search\s+maar(?:o|na)?\s+)(.+?)(?:\s+(?:and|then|aur|phir)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip(" ,.-")
            break

    text = re.sub(
        r"\b(?:aur|and|then|phir)\b.*\b(?:results?|analy[sz]e|compare|scroll|batao|batayein|batana)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:open|kholo|khol|website|flipkart|amazon|par|pe|mein|me|on|karo|karein|karna|please)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if text:
        return text
    if re.search(r"\b(?:karein|karo|analy[sz]e|results?|batayein|batao|phir|aur)\b", str(goal or ""), re.IGNORECASE):
        return ""
    return goal


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=PLANNER_PROMPT
    )

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        response = model.generate_content(prompt)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan     = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)
