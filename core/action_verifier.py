"""Evidence-based post-action verification for visible desktop work."""
from __future__ import annotations
import io
import re

from core import local_vision
from core.ai_providers import get_router
from core.visual_diff import change_score


def _capture() -> bytes:
    import mss
    import mss.tools
    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)


def verify_visible_action(action: str, result: str, before: bytes | None = None) -> tuple[str, str]:
    """Return (PASS|FAIL|UNCERTAIN, evidence) without inventing success."""
    try:
        image = _capture()
        diff_note = ""
        if before:
            diff_note = f" Screen-change score: {change_score(before, image):.3f}."
        prompt = (
            "You are a strict UI verifier. Inspect the screenshot and decide whether "
            "the requested visible action appears complete. Reply in exactly this form: "
            "PASS: short evidence, FAIL: short evidence, or UNCERTAIN: short evidence. "
            "Do not infer hidden state and do not assume success from the action text.\n"
            f"Requested action: {action}\nTool response: {result[:500]}.{diff_note}"
        )
        try:
            answer = local_vision.analyze(image, prompt, max_tokens=60)
        except Exception as local_err:
            # The local model is fast and private when available; use the
            # configured vision router as a reliable fallback for verification.
            print(f"[Verifier] Local vision unavailable, using routed vision: {local_err}")
            answer = get_router().run_vision(image, prompt)
        match = re.search(r"\b(PASS|FAIL|UNCERTAIN)\s*:\s*(.*)", answer or "", re.I | re.S)
        if not match:
            return "UNCERTAIN", "Verifier returned an unstructured result."
        return match.group(1).upper(), re.sub(r"\s+", " ", match.group(2)).strip()[:300]
    except Exception as exc:
        return "UNCERTAIN", f"Visual verification unavailable: {exc}"
