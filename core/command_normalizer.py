"""Conservative cleanup for noisy Hinglish voice transcripts."""
from __future__ import annotations

import re


_ALIASES = {
    "khol do": "open",
    "kholo": "open",
    "khol": "open",
    "chalao": "open",
    "bajao": "play",
    "dhundho": "search for",
    "dhundo": "search for",
    "dhund": "search for",
    "batao": "tell me",
    "btao": "tell me",
    "band karo": "close",
    "rok do": "stop",
    "ruko": "stop",
}


def normalize_command(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    # Replace only complete phrases, preserving app names and user content.
    for source, target in sorted(_ALIASES.items(), key=lambda item: -len(item[0])):
        value = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, value, flags=re.I)
    value = re.sub(r"^search\s+search\s+for\s+", "search for ", value, flags=re.I)
    value = re.sub(r"^(.+?)\s+open$", r"open \1", value, flags=re.I)
    return value
