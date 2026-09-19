import json
import sys
import threading
from datetime import datetime
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE              = get_base_dir()
_CONFIG_PATH       = _BASE / "config" / "api_keys.json"
_MEMORY_PATH       = _BASE / "memory" / "long_term.json"
_PREDICT_LOG_PATH  = _BASE / "memory" / "prediction_log.json"

_MAX_LOG_ENTRIES   = 60   # keep last 60 interactions for pattern learning


def _get_api_key() -> str:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_memory() -> dict:
    try:
        if _MEMORY_PATH.exists():
            return json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_log() -> list:
    try:
        if _PREDICT_LOG_PATH.exists():
            return json.loads(_PREDICT_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_log(log: list) -> None:
    _PREDICT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if len(log) > _MAX_LOG_ENTRIES:
        log = log[-_MAX_LOG_ENTRIES:]
    _PREDICT_LOG_PATH.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def log_interaction(user_text: str, tool_used: str, result_summary: str = "") -> None:
    """Call this after every Jarvis action to build pattern history."""
    log = _load_log()
    log.append({
        "time":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "hour":    datetime.now().hour,
        "day":     datetime.now().strftime("%A"),
        "user":    user_text[:120],
        "tool":    tool_used,
        "result":  result_summary[:80],
    })
    _save_log(log)


def _ask_ai(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash")
    return model.generate_content(prompt).text.strip()


def _predict(recent_context: str, log_summary: str, memory_summary: str) -> dict:
    prompt = (
        "You are the prediction module of JARVIS, an AI assistant.\n"
        "Based on the user's recent actions, behavioral patterns, and personal context, "
        "predict their SINGLE most likely NEXT action.\n\n"
        f"Recent conversation / last action:\n{recent_context}\n\n"
        f"Behavioral patterns (past interactions):\n{log_summary}\n\n"
        f"User context from memory:\n{memory_summary}\n\n"
        "Available tools:\n"
        "- computer_control: click, smart_click, screen_find, screen_click, type, move, hotkey, press, scroll, screenshot, focus_window\n"
        "- browser_control: click, smart_click, smart_type, fill_form, new_tab, close_tab, get_state, get_url\n"
        "- screen_process: inspect screen or camera for visual context and OCR\n"
        "Choose the tool that will most quickly complete the user's likely next step.\n"
        "Rules:\n"
        "- Predict only ONE next action.\n"
        "- If confidence is below 70%, do NOT suggest auto-execution.\n"
        "- Return ONLY valid JSON, no markdown:\n"
        '{"prediction": "...", "tool": "...", "parameters": {}, '
        '"confidence": 0-100, "auto_execute": true/false, '
        '"reason": "brief reason"}'
    )
    try:
        raw  = _ask_ai(prompt)
        raw  = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[Predict] ⚠️ Parse failed: {e}")
        return {"prediction": "unknown", "tool": "", "parameters": {},
                "confidence": 0, "auto_execute": False, "reason": "parse error"}


def _format_log_summary(log: list) -> str:
    if not log:
        return "No history yet."
    now_hour = datetime.now().hour
    now_day  = datetime.now().strftime("%A")

    # find most used tools at this hour/day
    relevant = [e for e in log if abs(e.get("hour", 0) - now_hour) <= 1
                or e.get("day") == now_day]
    recent   = log[-10:]

    tool_freq: dict[str, int] = {}
    for e in log:
        t = e.get("tool", "")
        if t:
            tool_freq[t] = tool_freq.get(t, 0) + 1

    top_tools = sorted(tool_freq.items(), key=lambda x: x[1], reverse=True)[:5]

    lines = [f"Top tools overall: {', '.join(f'{t}({c})' for t,c in top_tools)}"]
    if relevant:
        lines.append(f"At this time/day usually: {', '.join(set(e['tool'] for e in relevant[-5:]))}")
    lines.append("Last 5 actions: " + " → ".join(e.get("tool","?") for e in recent[-5:]))
    return "\n".join(lines)


def predictive_action(parameters: dict = None, player=None, speak=None) -> str:
    params  = parameters or {}
    action  = params.get("action", "predict").lower()
    context = params.get("context", "").strip()
    confirm = str(params.get("confirm", "false")).lower() in ("true", "yes", "1")

    # ── PREDICT ──────────────────────────────────────────────────
    if action == "predict":
        log     = _load_log()
        memory  = _load_memory()

        mem_lines = []
        for cat, items in memory.items():
            if isinstance(items, dict):
                for k, v in list(items.items())[:3]:
                    val = v.get("value", v) if isinstance(v, dict) else v
                    mem_lines.append(f"  {k}: {val}")
        memory_summary = "\n".join(mem_lines) if mem_lines else "No memory data."

        log_summary = _format_log_summary(log)
        result      = _predict(context or "User just finished an action.", log_summary, memory_summary)

        prediction  = result.get("prediction", "")
        confidence  = result.get("confidence", 0)
        auto_exec   = result.get("auto_execute", False)
        tool        = result.get("tool", "")
        pred_params = result.get("parameters", {})
        reason      = result.get("reason", "")

        msg = f"🔮 Prediction: {prediction} (confidence: {confidence}%)\nReason: {reason}"

        if auto_exec and confidence >= 70 and tool:
            if speak:
                speak(f"Mujhe lagta hai aap {prediction} karna chahte hain. Kar deta hun.")
            try:
                exec_result = _auto_execute(tool, pred_params, speak)
                msg += f"\n✅ Auto-executed: {tool} → {exec_result}"
                log_interaction("auto-predicted", tool, exec_result[:60])
            except Exception as e:
                msg += f"\n❌ Auto-execute failed: {e}"
        elif confidence >= 50 and tool:
            msg += f"\n💡 Suggested next: {tool} — say 'haan karo' to execute."
        else:
            msg += "\nNot confident enough to suggest auto-action."

        return msg

    # ── CONFIRM / EXECUTE LAST PREDICTION ────────────────────────
    if action == "confirm":
        context_tool   = params.get("tool", "")
        context_params = params.get("parameters", {})
        if not context_tool:
            return "Kaunsa tool execute karun? 'tool' parameter do."
        try:
            result = _auto_execute(context_tool, context_params, speak)
            log_interaction("user-confirmed-prediction", context_tool, result[:60])
            return f"Done: {result}"
        except Exception as e:
            return f"Execution failed: {e}"

    # ── VIEW PATTERN LOG ─────────────────────────────────────────
    if action == "log":
        log = _load_log()
        if not log:
            return "Abhi tak koi interaction log nahi hai."
        lines = [f"[{e['time']}] {e['tool']} — {e['user'][:50]}" for e in log[-15:]]
        return "Last 15 interactions:\n" + "\n".join(lines)

    # ── CLEAR LOG ────────────────────────────────────────────────
    if action == "clear":
        _save_log([])
        return "Prediction log cleared."

    return f"Unknown action: {action}. Use: predict | confirm | log | clear"


def _auto_execute(tool: str, tool_params: dict, speak=None) -> str:
    """Execute a predicted tool action directly."""
    print(f"[Predict] ⚡ Auto-executing: {tool}  {tool_params}")

    if tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=tool_params, player=None) or "Done."

    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=tool_params, player=None) or "Done."

    if tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=tool_params, player=None) or "Done."

    if tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=tool_params, player=None) or "Done."

    if tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=tool_params, player=None) or "Done."

    if tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=tool_params, player=None) or "Done."

    if tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=tool_params, player=None) or "Done."

    if tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=tool_params, player=None) or "Done."

    if tool == "study_assistant":
        from actions.study_assistant import study_assistant
        return study_assistant(parameters=tool_params, player=None, speak=speak) or "Done."

    return f"Tool '{tool}' auto-execution not supported. User can call it manually."
