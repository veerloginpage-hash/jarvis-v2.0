"""Load self-evolved Jarvis tools from actions/evolved/<feature>/"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


EVOLVED_ROOT = get_base_dir() / "actions" / "evolved"


def _load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load {module_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_evolved_tools() -> tuple[list[dict], dict[str, Callable[..., str]]]:
    """
    Returns (tool_declarations for Gemini, name -> handler).
    Each feature folder must contain manifest.json and tool.py with run().
    """
    declarations: list[dict] = []
    handlers: dict[str, Callable[..., str]] = {}

    if not EVOLVED_ROOT.is_dir():
        EVOLVED_ROOT.mkdir(parents=True, exist_ok=True)
        return declarations, handlers

    for feature_dir in sorted(EVOLVED_ROOT.iterdir()):
        if not feature_dir.is_dir() or feature_dir.name.startswith("_"):
            continue

        manifest_path = feature_dir / "manifest.json"
        tool_path     = feature_dir / "tool.py"
        if not manifest_path.exists() or not tool_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name     = manifest.get("name") or feature_dir.name
            if name in handlers:
                print(f"[EvolvedLoader] ⚠️ Duplicate tool name skipped: {name}")
                continue

            mod = _load_module(tool_path, f"jarvis_evolved_{name}")
            run_fn = getattr(mod, "run", None)
            if not callable(run_fn):
                print(f"[EvolvedLoader] ⚠️ No run() in {tool_path}")
                continue

            decl = {
                "name": name,
                "description": manifest.get(
                    "description",
                    f"Evolved Jarvis capability: {name}",
                ),
                "parameters": manifest.get("parameters", {
                    "type": "OBJECT",
                    "properties": {
                        "instruction": {
                            "type": "STRING",
                            "description": "What to do with this capability",
                        }
                    },
                    "required": [],
                }),
            }
            declarations.append(decl)
            handlers[name] = run_fn
            print(f"[EvolvedLoader] ✅ {name} from {feature_dir.name}")

        except Exception as e:
            print(f"[EvolvedLoader] ⚠️ Failed {feature_dir.name}: {e}")

    return declarations, handlers


def reload_evolved() -> tuple[list[dict], dict[str, Callable[..., str]]]:
    """Fresh load (e.g. after evolution agent adds a feature)."""
    return load_evolved_tools()
