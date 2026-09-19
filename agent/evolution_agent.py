"""
Jarvis Evolution Agent — internal self-improvement loop.

- Runs once per day while Jarvis is open (adds a small new tool under actions/evolved/)
- Queues rescue cycles when main tools fail
- Does not modify main.py; evolved tools are loaded dynamically
"""

import ast
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from agent.evolved_loader import EVOLVED_ROOT, get_base_dir, reload_evolved

BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
STATE_PATH       = BASE_DIR / "memory" / "evolution_state.json"
ACTIONS_DIR      = BASE_DIR / "actions"
MODEL            = "gemini-2.5-flash"

ALLOWED_STDLIB = {
    "json", "re", "os", "sys", "pathlib", "datetime", "time", "math",
    "random", "string", "collections", "subprocess", "platform", "threading",
    "shutil", "tempfile", "hashlib", "base64", "urllib", "csv", "io",
    "typing", "enum", "functools", "itertools", "statistics",
}
ALLOWED_THIRD_PARTY = {
    "requests", "PIL", "pillow", "pyautogui", "pyperclip", "psutil",
    "numpy", "cv2", "mss", "bs4", "duckduckgo_search",
}

EVOLUTION_PROMPT = """You are the Evolution Agent inside JARVIS — you improve JARVIS itself.

Given the codebase context, recent failures, and existing evolved tools, design ONE small,
safe, useful new capability JARVIS is missing. Prefer practical daily-use utilities.

RULES:
- Output ONLY valid JSON (no markdown).
- Tool must be self-contained in tool.py with: def run(parameters: dict, player=None, speak=None) -> str
- Use only stdlib or: requests, pillow/PIL, pyautogui, pyperclip, psutil, pathlib, subprocess
- No network calls except requests.get with timeout<=10 if truly needed
- No file writes outside user's Desktop, Documents, Downloads, or JARVIS memory folder
- No os.system with shell=True, no eval/exec, no deleting files
- Max ~120 lines of code
- feature_id: snake_case, unique, not in existing_evolved list

JSON schema:
{
  "feature_id": "quick_focus_timer",
  "title": "Short human title",
  "description": "When Jarvis should call this tool (1-2 sentences)",
  "manifest": {
    "name": "same_as_feature_id",
    "description": "...",
    "parameters": {
      "type": "OBJECT",
      "properties": { "minutes": {"type": "INTEGER", "description": "..."} },
      "required": ["minutes"]
    }
  },
  "code": "full tool.py source as a single string with \\n escapes"
}
"""


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_state() -> dict:
    default = {
        "last_daily_run": "",
        "features_added": [],
        "pending_rescues": [],
        "last_status": "",
    }
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            default.update(data)
    except Exception:
        pass
    return default


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _list_builtin_actions() -> list[str]:
    names = []
    if ACTIONS_DIR.is_dir():
        for p in ACTIONS_DIR.glob("*.py"):
            if p.name != "__init__.py":
                names.append(p.stem)
    return sorted(names)


def _list_evolved_ids() -> list[str]:
    ids = []
    if EVOLVED_ROOT.is_dir():
        for d in EVOLVED_ROOT.iterdir():
            if d.is_dir() and (d / "tool.py").exists():
                ids.append(d.name)
    return ids


def _validate_code(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_STDLIB and root not in ALLOWED_THIRD_PARTY:
                    return f"Disallowed import: {root}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root not in ALLOWED_STDLIB and root not in ALLOWED_THIRD_PARTY:
                    return f"Disallowed import: {root}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
                return "exec/eval not allowed"

    if "shell=True" in code:
        return "shell=True not allowed"
    if re.search(r"\bos\.system\s*\(", code):
        return "os.system not allowed"

    return None


def _collect_context(extra: str = "") -> str:
    state = _load_state()
    failures = state.get("pending_rescues", [])[-5:]
    added    = state.get("features_added", [])[-10:]

    lines = [
        f"Date: {datetime.now().isoformat()}",
        f"OS: built-in actions: {', '.join(_list_builtin_actions()[:30])}",
        f"Evolved tools already installed: {', '.join(_list_evolved_ids()) or '(none)'}",
        f"Recently added by evolution: {', '.join(added) or '(none)'}",
    ]
    if failures:
        lines.append("Recent tool failures (build fix or workaround):")
        for f in failures:
            lines.append(f"  - {f.get('tool')}: {str(f.get('error', ''))[:120]}")
    if extra:
        lines.append(f"Extra context: {extra}")
    return "\n".join(lines)


def _generate_feature(context: str, mode: str) -> dict | None:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name=MODEL,
        system_instruction=EVOLUTION_PROMPT,
    )

    user = f"Mode: {mode}\n\n{context}\n\nReturn ONE new feature JSON."
    try:
        response = model.generate_content(user)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        return json.loads(text)
    except Exception as e:
        print(f"[Evolution] ⚠️ Generation failed: {e}")
        return None


def _install_feature(payload: dict) -> tuple[bool, str]:
    feature_id = (payload.get("feature_id") or payload.get("manifest", {}).get("name") or "").strip()
    if not feature_id or not re.match(r"^[a-z][a-z0-9_]{2,40}$", feature_id):
        return False, "Invalid feature_id"

    if feature_id in _list_evolved_ids():
        return False, f"Feature {feature_id} already exists"

    code = payload.get("code", "")
    if not isinstance(code, str):
        return False, "code must be a string"
    code = code.replace("\\n", "\n").strip()
    if not code.startswith("def run"):
        if "def run(" not in code:
            return False, "code must define run(parameters, player=None, speak=None)"

    err = _validate_code(code)
    if err:
        return False, err

    manifest = payload.get("manifest") or {}
    manifest.setdefault("name", feature_id)
    manifest.setdefault("description", payload.get("description", feature_id))

    feature_dir = EVOLVED_ROOT / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "tool.py").write_text(code, encoding="utf-8")
    (feature_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True, feature_id


class EvolutionAgent:
    """Background self-improvement for Jarvis."""

    def __init__(
        self,
        on_tools_updated: Callable[[], None] | None = None,
        ui_log: Callable[[str], None] | None = None,
        daily_hour: int = 3,
    ):
        self._on_tools_updated = on_tools_updated
        self._ui_log           = ui_log
        self._daily_hour       = daily_hour
        self._lock             = threading.Lock()
        self._running          = False
        self._thread: threading.Thread | None = None
        self._stop             = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="EvolutionAgent",
        )
        self._thread.start()
        # Keep the background worker from crashing on Windows consoles that
        # still expose a CP1252 stdout stream.
        print("[Evolution] Background agent started")

    def stop(self) -> None:
        self._stop.set()

    def _log(self, msg: str) -> None:
        print(f"[Evolution] {msg}")
        if self._ui_log:
            try:
                self._ui_log(f"EVO: {msg}")
            except Exception:
                pass

    def report_failure(self, tool_name: str, error: str, args: str = "") -> None:
        state = _load_state()
        rescues = state.setdefault("pending_rescues", [])
        rescues.append({
            "time":  datetime.now().isoformat(),
            "tool":  tool_name,
            "error": str(error)[:300],
            "args":  str(args)[:200],
        })
        if len(rescues) > 20:
            state["pending_rescues"] = rescues[-20:]
        _save_state(state)

        # Trigger rescue if same tool failed 2+ times recently
        recent = [r for r in rescues[-8:] if r.get("tool") == tool_name]
        if len(recent) >= 2:
            threading.Thread(
                target=self.run_cycle,
                kwargs={"mode": "rescue", "context": f"Fix repeated failures in {tool_name}"},
                daemon=True,
            ).start()

    def status(self) -> str:
        state = _load_state()
        evolved = _list_evolved_ids()
        pending = len(state.get("pending_rescues", []))
        last    = state.get("last_daily_run") or "never"
        return (
            f"Evolution agent active. Evolved tools: {len(evolved)} "
            f"({', '.join(evolved[-5:]) or 'none'}). "
            f"Last daily run: {last}. Pending failure hints: {pending}."
        )

    def run_cycle(self, mode: str = "daily", context: str = "") -> str:
        with self._lock:
            if self._running:
                return "Evolution cycle already running."
            self._running = True

        try:
            state = _load_state()
            today = datetime.now().strftime("%Y-%m-%d")

            if mode == "daily" and state.get("last_daily_run") == today:
                return f"Already evolved today ({today})."

            ctx = _collect_context(context)
            payload = _generate_feature(ctx, mode)
            if not payload:
                msg = "Could not generate a feature (API or parse error)."
                state["last_status"] = msg
                _save_state(state)
                return msg

            ok, result = _install_feature(payload)
            if not ok:
                state["last_status"] = result
                _save_state(state)
                self._log(f"❌ Install failed: {result}")
                return result

            title = payload.get("title", result)
            state.setdefault("features_added", []).append({
                "id": result, "title": title, "date": today, "mode": mode,
            })
            if mode == "daily":
                state["last_daily_run"] = today
            if mode == "rescue" and state.get("pending_rescues"):
                state["pending_rescues"] = state["pending_rescues"][-3:]

            state["last_status"] = f"Added {result}: {title}"
            _save_state(state)

            reload_evolved()
            if self._on_tools_updated:
                self._on_tools_updated()

            msg = (
                f"Naya feature add ho gaya: {result} ({title}). "
                "Agla reconnect par Jarvis ise use kar sakta hai."
            )
            self._log(f"✅ {msg}")
            return msg

        finally:
            with self._lock:
                self._running = False

    def _scheduler_loop(self) -> None:
        # First check after 2 minutes (catch up if missed yesterday)
        time.sleep(120)

        while not self._stop.is_set():
            try:
                now = datetime.now()
                state = _load_state()
                today = now.strftime("%Y-%m-%d")

                if state.get("last_daily_run") != today:
                    if now.hour >= self._daily_hour or now.hour < 1:
                        self._log("🌅 Daily evolution cycle starting...")
                        self.run_cycle(mode="daily")
            except Exception as e:
                print(f"[Evolution] ⚠️ Scheduler: {e}")

            self._stop.wait(3600)  # check every hour


_agent: EvolutionAgent | None = None


def get_evolution_agent(
    on_tools_updated: Callable[[], None] | None = None,
    ui_log: Callable[[str], None] | None = None,
) -> EvolutionAgent:
    global _agent
    if _agent is None:
        _agent = EvolutionAgent(on_tools_updated=on_tools_updated, ui_log=ui_log)
    return _agent


def evolution_agent_tool(
    parameters: dict,
    player=None,
    speak=None,
) -> str:
    action  = (parameters.get("action") or "status").lower()
    context = parameters.get("context") or parameters.get("description") or ""
    agent   = get_evolution_agent()

    if action in ("status", "info"):
        return agent.status()
    if action in ("run", "run_now", "evolve", "daily"):
        return agent.run_cycle(mode="daily", context=context)
    if action in ("rescue", "fix", "fix_failure"):
        tool = parameters.get("tool_name") or parameters.get("tool") or ""
        ctx  = f"Rescue for tool {tool}. {context}"
        return agent.run_cycle(mode="rescue", context=ctx)
    if action == "suggest":
        ctx = _collect_context(context)
        payload = _generate_feature(ctx, "suggest_only")
        if not payload:
            return "Abhi suggestion generate nahi ho payi."
        return (
            f"Suggestion: {payload.get('title', payload.get('feature_id', '?'))} — "
            f"{payload.get('description', '')}"
        )
    return agent.status()
