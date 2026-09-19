"""Small, dependency-light screen change detector for action evidence."""
from __future__ import annotations

import io


def capture_screen() -> bytes:
    import mss
    import mss.tools
    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)


def change_score(before: bytes, after: bytes) -> float:
    """Return a normalized 0..1 visual difference score."""
    try:
        from PIL import Image, ImageChops, ImageStat
        first = Image.open(io.BytesIO(before)).convert("L")
        second = Image.open(io.BytesIO(after)).convert("L").resize(first.size)
        mean = ImageStat.Stat(ImageChops.difference(first, second)).mean[0]
        return min(1.0, mean / 32.0)
    except Exception:
        return 0.0


def changed(before: bytes, after: bytes, threshold: float = 0.025) -> bool:
    return change_score(before, after) >= threshold
