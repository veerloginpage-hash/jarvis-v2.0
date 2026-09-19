"""Free, deterministic Windows automation workflows.

This runner intentionally has no model or network dependency. Workflows use
native UI Automation labels first, so a saved routine repeats reliably instead
of asking an AI to guess new coordinates on every run.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from actions.computer_control import computer_control, _uia_find

ROOT = Path.home() / ".jarvis" / "workflows"
REGISTRY = ROOT / "workflows.json"
_lock = threading.RLock()
_ALLOWED = {"open_app", "focus_window", "click", "type", "press", "hotkey", "scroll", "wait", "assert_visible"}


def _load() -> dict[str, Any]:
    ROOT.mkdir(parents=True, exist_ok=True)
    if not REGISTRY.exists():
        return {"workflows": []}
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data.get("workflows"), list) else {"workflows": []}
    except (OSError, json.JSONDecodeError):
        return {"workflows": []}


def _save(data: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, REGISTRY)


def _validate(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= 30:
        raise ValueError("A workflow needs 1 to 30 steps.")
    clean = []
    for index, raw in enumerate(steps, 1):
        if not isinstance(raw, dict) or raw.get("action") not in _ALLOWED:
            raise ValueError(f"Step {index} has an unsupported action.")
        step = dict(raw)
        if step["action"] in {"click", "assert_visible"} and not str(step.get("target", "")).strip():
            raise ValueError(f"Step {index} needs a visible target label.")
        if step["action"] == "type" and "text" not in step:
            raise ValueError(f"Step {index} needs text.")
        clean.append(step)
    return clean


def _run_step(step: dict[str, Any]) -> str:
    action = step["action"]
    if action == "open_app":
        from actions.open_app import open_app
        return open_app({"app_name": step.get("app_name", "")}, player=None) or "Opened app."
    if action == "assert_visible":
        return "Visible." if _uia_find(step["target"]) else f"NOT_VISIBLE: {step['target']}"
    if action == "wait":
        seconds = max(0.1, min(float(step.get("seconds", 1)), 20))
        time.sleep(seconds)
        return f"Waited {seconds:g}s."
    if action == "click":
        coords = _uia_find(step["target"])
        if not coords:
            return f"NOT_FOUND: {step['target']}"
        return computer_control({"action": "click", "x": coords[0], "y": coords[1]}, player=None)
    params = {"action": action}
    if action == "focus_window": params["title"] = step.get("title", "")
    if action == "type": params.update({"action": "smart_type", "text": step.get("text", "")})
    if action == "press": params["key"] = step.get("key", "enter")
    if action == "hotkey": params["keys"] = step.get("keys", "")
    if action == "scroll": params.update({"direction": step.get("direction", "down"), "amount": step.get("amount", 3)})
    return computer_control(params, player=None) or "Done."


def local_workflow(parameters: dict, player=None, **_: Any) -> str:
    action = str(parameters.get("action", "list")).lower().strip()
    with _lock:
        data = _load()
        if action == "list":
            return json.dumps([{k: item[k] for k in ("id", "name", "steps", "last_result")} for item in data["workflows"]], ensure_ascii=False)
        if action == "create":
            name = " ".join(str(parameters.get("name", "")).split())
            if not name or len(name) > 80: return "Choose a workflow name between 1 and 80 characters."
            try: steps = _validate(parameters.get("steps"))
            except ValueError as exc: return str(exc)
            if any(item["name"].casefold() == name.casefold() for item in data["workflows"]): return "A workflow with that name already exists."
            item = {"id": uuid.uuid4().hex[:10], "name": name, "steps": steps, "last_result": None}
            data["workflows"].append(item); _save(data)
            return f"Local workflow '{name}' saved with {len(steps)} steps."
        item = next((entry for entry in data["workflows"] if entry["id"] == str(parameters.get("id", ""))), None)
        if not item: return "Workflow not found. Use list to get its id."
        if action == "delete":
            data["workflows"].remove(item); _save(data); return f"Workflow '{item['name']}' deleted."
        if action != "run": return "Action must be create, list, run, or delete."
    results = []
    for index, step in enumerate(item["steps"], 1):
        result = _run_step(step)
        results.append(f"{index}. {result}")
        if str(result).startswith(("NOT_FOUND", "NOT_VISIBLE")):
            break
    with _lock:
        data = _load(); current = next((entry for entry in data["workflows"] if entry["id"] == item["id"]), None)
        if current: current["last_result"] = results[-1] if results else "No steps run"; _save(data)
    return "\n".join(results)
