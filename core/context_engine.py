"""Fast, thread-safe session context for multi-turn Jarvis commands."""
from __future__ import annotations

import threading
import time
from collections import deque


class SessionContext:
    """Keep only high-value short-term context; intentionally memory-only."""

    def __init__(self, max_events: int = 20):
        self._lock = threading.RLock()
        self._goal = ""
        self._active_app = ""
        self._browser = ""
        self._url = ""
        self._last_tool = ""
        self._last_result = ""
        self._events = deque(maxlen=max_events)

    def user_command(self, text: str) -> None:
        value = " ".join(str(text or "").split()).strip()
        if not value:
            return
        with self._lock:
            self._goal = value
            self._events.append({"kind": "user", "text": value, "ts": time.time()})

    def tool_started(self, tool: str, args: dict) -> None:
        with self._lock:
            self._last_tool = str(tool or "")
            action = str((args or {}).get("action", "") or "")
            if tool in {"browser_control", "ai_browser"}:
                self._browser = str((args or {}).get("browser", "") or self._browser)
                self._url = str((args or {}).get("url", "") or self._url)
            if tool in {"open_app", "computer_control", "ai_desktop_control"}:
                self._active_app = str((args or {}).get("app_name", "") or self._active_app)
            self._events.append({"kind": "tool", "tool": tool, "action": action, "ts": time.time()})

    def tool_finished(self, result: object) -> None:
        with self._lock:
            self._last_result = str(result or "")[:500]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "goal": self._goal,
                "active_app": self._active_app,
                "browser": self._browser,
                "url": self._url,
                "last_tool": self._last_tool,
                "last_result": self._last_result,
                "recent_events": list(self._events)[-8:],
            }

    def prompt_context(self) -> str:
        state = self.snapshot()
        return (
            f"Active goal: {state['goal'] or 'none'}\n"
            f"Active app: {state['active_app'] or 'unknown'}\n"
            f"Browser/site: {state['browser'] or 'unknown'} {state['url'] or ''}\n"
            f"Last action: {state['last_tool'] or 'none'}\n"
            f"Last result: {state['last_result'] or 'none'}"
        )


_context = SessionContext()


def get_context() -> SessionContext:
    return _context
