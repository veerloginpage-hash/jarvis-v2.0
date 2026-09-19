"""Durable runtime ledger for observable and resumable Jarvis tasks."""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "memory" / "runtime"
ROOT.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()

def _write(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def record(task_id: str, event: str, **details) -> None:
    try:
        item = {"time": time.time(), "task_id": task_id, "event": event, **details}
        path = ROOT / "history.jsonl"
        with _lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2000:
                path.write_text("\n".join(lines[-2000:]) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[Runtime] history write skipped: {exc}")

def checkpoint(task_id: str, state: dict) -> None:
    try:
        with _lock:
            _write(ROOT / f"checkpoint_{task_id}.json", {"time": time.time(), **state})
    except Exception as exc:
        print(f"[Runtime] checkpoint skipped: {exc}")

def load_checkpoint(task_id: str) -> dict | None:
    path = ROOT / f"checkpoint_{task_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None

def clear_checkpoint(task_id: str) -> None:
    try:
        (ROOT / f"checkpoint_{task_id}.json").unlink(missing_ok=True)
    except Exception:
        pass
