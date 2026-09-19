"""Thread-safe in-memory performance counters for the current session."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class PerformanceTracker:
    def __init__(self, recent_limit: int = 40):
        self._lock = threading.Lock()
        self._counts = defaultdict(lambda: {"ok": 0, "failed": 0, "total_ms": 0.0})
        self._recent = deque(maxlen=recent_limit)

    def record(self, tool: str, elapsed_ms: float, failed: bool = False) -> None:
        name = str(tool or "unknown")
        with self._lock:
            stats = self._counts[name]
            stats["failed" if failed else "ok"] += 1
            stats["total_ms"] += max(0.0, float(elapsed_ms))
            self._recent.append({
                "tool": name,
                "elapsed_ms": round(float(elapsed_ms), 1),
                "failed": bool(failed),
                "time": time.time(),
            })

    def snapshot(self) -> dict:
        with self._lock:
            counts = {}
            for tool, stats in self._counts.items():
                total = stats["ok"] + stats["failed"]
                counts[tool] = {
                    **stats,
                    "calls": total,
                    "avg_ms": round(stats["total_ms"] / total, 1) if total else 0.0,
                }
            return {"tools": counts, "recent": list(self._recent)}


_tracker = PerformanceTracker()


def get_tracker() -> PerformanceTracker:
    return _tracker
