"""Durable, user-owned scheduled routines for J.A.R.V.I.S.

Automations are deliberately named and recorded locally: there are no hidden
background actions.  The OS scheduler only wakes this small runner; the runner
then gives the approved goal to the normal verified task executor.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path.home() / ".jarvis" / "automations"
REGISTRY_PATH = DATA_ROOT / "automations.json"
HISTORY_PATH = DATA_ROOT / "history.jsonl"
_lock = threading.RLock()


def _ensure_storage() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_PATH.exists():
        _atomic_write(REGISTRY_PATH, {"version": 1, "automations": []})


def _atomic_write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load() -> dict[str, Any]:
    _ensure_storage()
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("automations"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "automations": []}


def _save(data: dict[str, Any]) -> None:
    _ensure_storage()
    _atomic_write(REGISTRY_PATH, data)


def _event(automation_id: str, event: str, **detail: Any) -> None:
    _ensure_storage()
    item = {"time": time.time(), "automation_id": automation_id, "event": event, **detail}
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _safe_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split()).strip()
    if not cleaned or len(cleaned) > 80:
        raise ValueError("Choose an automation name between 1 and 80 characters.")
    return cleaned


def _schedule_spec(schedule: str, run_at: str, time_of_day: str, day_of_week: str) -> dict[str, str]:
    schedule = str(schedule or "").lower().strip()
    if schedule not in {"once", "daily", "weekly"}:
        raise ValueError("Schedule must be once, daily, or weekly.")
    if schedule == "once":
        when = datetime.strptime(run_at, "%Y-%m-%d %H:%M")
        if when <= datetime.now():
            raise ValueError("A one-time automation must be scheduled in the future.")
        return {"kind": schedule, "run_at": when.strftime("%Y-%m-%d %H:%M")}
    datetime.strptime(time_of_day, "%H:%M")
    spec = {"kind": schedule, "time": time_of_day}
    if schedule == "weekly":
        day = str(day_of_week or "").lower().strip()
        if day not in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
            raise ValueError("Weekly automations need a weekday such as monday.")
        spec["day"] = day
    return spec


def _task_name(automation_id: str) -> str:
    return f"JARVIS_Automation_{automation_id}"


def _command(automation_id: str) -> str:
    # sys.executable preserves the installed application's environment.
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --run {automation_id}'


def _register_windows(item: dict[str, Any]) -> None:
    spec = item["schedule"]
    args = ["schtasks", "/Create", "/TN", _task_name(item["id"]), "/TR", _command(item["id"]), "/F", "/RL", "LIMITED"]
    if spec["kind"] == "once":
        when = datetime.strptime(spec["run_at"], "%Y-%m-%d %H:%M")
        args += ["/SC", "ONCE", "/SD", when.strftime("%m/%d/%Y"), "/ST", when.strftime("%H:%M")]
    elif spec["kind"] == "daily":
        args += ["/SC", "DAILY", "/ST", spec["time"]]
    else:
        args += ["/SC", "WEEKLY", "/D", spec["day"][:3].upper(), "/ST", spec["time"]]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Task Scheduler rejected the routine.").strip())


def _register(item: dict[str, Any]) -> None:
    if sys.platform == "win32":
        _register_windows(item)
        return
    raise RuntimeError("Scheduled automations are currently available on Windows in this build.")


def _unregister(automation_id: str) -> None:
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/Delete", "/TN", _task_name(automation_id), "/F"], capture_output=True, text=True, check=False)


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("id", "name", "goal", "schedule", "status", "created_at", "last_run_at", "last_result")}


def _run(automation_id: str) -> str:
    with _lock:
        data = _load()
        item = next((entry for entry in data["automations"] if entry["id"] == automation_id), None)
        if not item:
            return "Automation was not found."
        if item["status"] != "active":
            return f"Automation '{item['name']}' is paused."
    _event(automation_id, "started")
    try:
        if str(APP_ROOT) not in sys.path:
            sys.path.insert(0, str(APP_ROOT))
        from agent.executor import AgentExecutor
        result = AgentExecutor().execute(item["goal"], runtime_id=f"automation-{automation_id}-{int(time.time())}")
        state = "completed"
    except Exception as exc:  # Background jobs must log a real failure, never fake success.
        result, state = f"Failed: {exc}", "failed"
    with _lock:
        data = _load()
        current = next((entry for entry in data["automations"] if entry["id"] == automation_id), None)
        if current:
            current["last_run_at"] = datetime.now().isoformat(timespec="seconds")
            current["last_result"] = str(result)[:500]
            _save(data)
    _event(automation_id, state, result=str(result)[:500])
    return str(result)


def automation(parameters: dict, response=None, player=None, **_: Any) -> str:
    """Create and manage transparent scheduled routines.

    create requires `approved: true`: this prevents an AI planner from silently
    creating unattended desktop actions.  The direct UI/voice layer can request
    that approval from the owner before calling this function.
    """
    action = str(parameters.get("action", "list")).lower().strip()
    run_id = ""
    with _lock:
        data = _load()
        if action == "list":
            items = [_public(item) for item in data["automations"]]
            return "No automations saved." if not items else json.dumps(items, ensure_ascii=False)
        if action == "create":
            if parameters.get("approved") is not True:
                return "Confirmation required: review the goal and explicitly approve this automation before it is scheduled."
            try:
                name = _safe_name(parameters.get("name", ""))
                goal = " ".join(str(parameters.get("goal", "")).split()).strip()
                if not goal or len(goal) > 1000:
                    raise ValueError("The automation needs a clear goal of up to 1000 characters.")
                spec = _schedule_spec(parameters.get("schedule"), parameters.get("run_at", ""), parameters.get("time", ""), parameters.get("day", ""))
                if any(entry["name"].casefold() == name.casefold() for entry in data["automations"]):
                    raise ValueError("An automation with that name already exists.")
                item = {"id": uuid.uuid4().hex[:12], "name": name, "goal": goal, "schedule": spec, "status": "active", "created_at": datetime.now().isoformat(timespec="seconds"), "last_run_at": None, "last_result": None}
                _register(item)
                data["automations"].append(item)
                _save(data)
                _event(item["id"], "created", name=name)
                return f"Automation '{name}' is active ({spec['kind']})."
            except (ValueError, RuntimeError) as exc:
                return f"Automation was not created: {exc}"
        automation_id = str(parameters.get("id", "")).strip()
        item = next((entry for entry in data["automations"] if entry["id"] == automation_id), None)
        if not item:
            return "Automation not found. Use list to get its id."
        if action == "run":
            # Leave the lock before execution: the routine itself updates its history.
            run_id = automation_id
        elif action == "pause":
            _unregister(automation_id); item["status"] = "paused"; _save(data); _event(automation_id, "paused")
            return f"Automation '{item['name']}' paused."
        elif action == "resume":
            try:
                _register(item); item["status"] = "active"; _save(data); _event(automation_id, "resumed")
                return f"Automation '{item['name']}' resumed."
            except RuntimeError as exc:
                return f"Automation could not resume: {exc}"
        elif action == "delete":
            _unregister(automation_id); data["automations"].remove(item); _save(data); _event(automation_id, "deleted")
            return f"Automation '{item['name']}' deleted."
        else:
            return "Action must be create, list, run, pause, resume, or delete."
    return _run(run_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", dest="automation_id")
    args = parser.parse_args()
    if args.automation_id:
        print(_run(args.automation_id))


if __name__ == "__main__":
    main()
