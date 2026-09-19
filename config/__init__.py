# config/__init__.py
import json, os
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"
_EXAMPLE_PATH = Path(__file__).parent / "api_keys.example.json"

def get_config() -> dict:
    target = _CONFIG_PATH if _CONFIG_PATH.is_file() else _EXAMPLE_PATH
    if target.is_file():
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", "windows").lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"