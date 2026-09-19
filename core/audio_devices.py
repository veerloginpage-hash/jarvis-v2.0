"""Best-effort audio device selection with safe default fallback."""
from __future__ import annotations

import json
from pathlib import Path


_WIRELESS_HINTS = ("airpods", "bluetooth", "buds", "headphone", "headset", "tws")


def _devices(kind: str) -> list[tuple[int, dict]]:
    try:
        import sounddevice as sd
        return [(i, d) for i, d in enumerate(sd.query_devices()) if d.get("max_" + kind, 0) > 0]
    except Exception:
        return []


def select_device(kind: str, preferred: str | None = None) -> int | None:
    """Return an index, preferring configured/name-matched devices."""
    devices = _devices(kind)
    if not devices:
        return None
    wanted = str(preferred or "").strip().lower()
    if wanted:
        for index, device in devices:
            if wanted in str(device.get("name", "")).lower():
                return index
    # If no explicit preference exists, keep the OS default. This avoids
    # unexpectedly switching from speakers to a nearby headset.
    try:
        import sounddevice as sd
        default = sd.default.device[0 if kind == "input" else 1]
        if default is not None and int(default) >= 0:
            return int(default)
    except Exception:
        pass
    return devices[0][0]


def configured_preference(base_dir: str | Path, kind: str) -> str:
    """Read an optional device name without ever making audio depend on it."""
    try:
        data = json.loads((Path(base_dir) / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return str(data.get(f"audio_{kind}_device", "") or "")
    except Exception:
        return ""


def describe() -> dict:
    result = {"inputs": [], "outputs": []}
    for kind, key in (("input", "inputs"), ("output", "outputs")):
        for index, device in _devices(kind):
            result[key].append({"index": index, "name": str(device.get("name", "unknown"))})
    return result
