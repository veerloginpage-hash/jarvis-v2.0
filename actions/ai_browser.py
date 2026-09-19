# actions/ai_browser.py
import os
import io
import time
import json
import base64
import re
from pathlib import Path
from PIL import Image
from actions.browser_control import _registry, _normalize_url, _clean_search_url
from core.ai_providers import get_router
from core import local_vision

async def get_page_screenshot_bytes(sess) -> bytes:
    """Takes a screenshot of the active page and returns the bytes."""
    page = await sess._get_page()
    return await page.screenshot(type="png", full_page=False)


async def get_page_viewport_size(sess) -> tuple[int, int]:
    """Return the real CSS viewport, including no_viewport/maximized windows."""
    page = await sess._get_page()
    size = await page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    return max(1, int(size.get("width", 1))), max(1, int(size.get("height", 1)))

def find_element_on_page_via_ai(sess, description: str) -> tuple[int, int] or None:
    """Sends page screenshot to Qwen VL to find coordinates of target description."""
    try:
        # Run inside the browser thread loop
        img_bytes = sess.run(get_page_screenshot_bytes(sess))
        
        # The screenshot can be a different pixel size from the CSS viewport
        # (DPI scaling/maximized windows). Ask vision about the image it sees,
        # then map its result back to Playwright CSS coordinates.
        image_w, image_h = Image.open(io.BytesIO(img_bytes)).size
        viewport_w, viewport_h = sess.run(get_page_viewport_size(sess))
        
        prompt = (
            f"This is a screenshot of the active browser webpage. Image dimensions are {image_w}x{image_h} pixels. "
            f"Find the UI element: '{description}'. "
            f"Respond ONLY with its center coordinates as x,y (e.g. 640,360) or 'NOT_FOUND' if not visible."
        )
        
        try:
            response = local_vision.analyze(img_bytes, prompt, max_tokens=80)
        except Exception:
            response = get_router().run_vision(img_bytes, prompt)
        
        text = response.strip()
        print(f"[AI Browser] AI element location: {text}")
        
        if "NOT_FOUND" in text.upper():
            return None
            
        # Accept plain x,y as well as the JSON emitted by newer vision models.
        match = re.search(r"[\"']?x[\"']?\s*:\s*(\d+).*?[\"']?y[\"']?\s*:\s*(\d+)", text, re.I | re.S)
        if not match:
            match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            x, y = int(match.group(1)), int(match.group(2))
            if not (0 <= x < image_w and 0 <= y < image_h):
                return None
            return round(x * viewport_w / image_w), round(y * viewport_h / image_h)
    except Exception as e:
        print(f"[AI Browser] Error in AI element find: {e}")
    return None


def _click_failed(result: object) -> bool:
    """Classify action responses instead of treating every non-empty string as success."""
    text = str(result or "").lower()
    failure_markers = (
        "could not", "not found", "requires a description", "no description",
        "failed", "error:", "timeout", "timed out", "unable",
    )
    return any(marker in text for marker in failure_markers)

def ai_browser(parameters: dict, player=None, speak=None) -> str:
    """
    Enhanced AI-powered browser automation.
    parameters:
      action: smart_navigate | smart_click | smart_type | scrape_page | tab_cleanup
      url/query: target url or search query
      description: element description (for click/type)
      text: text to type
      browser: chrome | edge | firefox etc.
    """
    action = parameters.get("action", "").lower().strip()
    browser_name = parameters.get("browser", "").lower().strip() or None
    
    if player:
        player.write_log(f"AI Browser: {action}")
        
    try:
        sess = _registry.get(browser_name)
    except Exception as e:
        return f"Could not access browser session: {e}"
        
    try:
        if action == "smart_navigate":
            target = parameters.get("url") or parameters.get("query", "")
            if not target:
                return "No URL or search query provided."
            
            if "://" in target or "." in target:
                url = _normalize_url(_clean_search_url(target))
                if speak:
                    speak(f"Sir, website open kar rha hoon: {url}")
                return sess.run(sess.go_to(url))
            else:
                if speak:
                    speak(f"Sir, Google par search kar rha hoon: {target}")
                return sess.run(sess.search(target, parameters.get("engine", "google")))
                
        elif action == "smart_click":
            desc = parameters.get("description", "")
            if not desc:
                return "No description provided for click."
                
            if speak:
                speak(f"Webpage par locate kar rha hoon: {desc}")
                
            # First try standard playwright matching, if it fails, fallback to AI vision
            res = sess.run(sess.smart_click(desc))
            if not _click_failed(res):
                return f"Standard click succeeded: {res}"
                
            # Fallback to AI screen search
            coords = find_element_on_page_via_ai(sess, desc)
            if coords:
                click_result = sess.run(sess.click_at(coords[0], coords[1]))
                if not _click_failed(click_result):
                    return f"AI vision click succeeded at {coords} for {desc}"
            return f"Could not find or click '{desc}' on webpage."
            
        elif action == "smart_type":
            desc = parameters.get("description", "")
            # Planners sometimes call this with query instead of text. Treat
            # both names identically so search boxes are not rejected early.
            text = parameters.get("text", "") or parameters.get("query", "")
            if not desc or not text:
                return "Description and text are required for typing."
                
            if speak:
                speak(f"Input field search kar rha hoon and write kar rha hoon.")
                
            # Try standard matching
            res = sess.run(sess.smart_type(desc, text))
            if not _click_failed(res):
                return f"Standard typing succeeded: {res}"
                
            # Fallback to AI vision click then type
            coords = find_element_on_page_via_ai(sess, desc)
            if coords:
                type_result = sess.run(sess.type_at(coords[0], coords[1], text))
                if not _click_failed(type_result):
                    return f"AI vision typing succeeded at input located at {coords}"
            return f"Could not locate input field '{desc}' on screen."
            
        elif action == "scrape_page":
            # Screenshot page, get content, summarize using OpenRouter
            page = sess.run(sess._get_page())
            html = sess.run(sess.get_text())
            
            prompt = (
                f"Extract the most relevant data and summarize the content of this webpage: \n\n"
                f"{html[:6000]}"
            )
            router = get_router()
            summary = router.generate_text(prompt, task_type="general")
            return f"Webpage Data Summary:\n{summary}"
            
        elif action == "tab_cleanup":
            # Switch between tabs and close duplicates/unwanted pages
            page = sess.run(sess._get_page())
            ctx = page.context
            pages = ctx.pages
            urls = [p.url for p in pages]
            
            prompt = (
                f"Here is a list of open tab URLs in the browser:\n"
                f"{json.dumps(urls)}\n"
                f"Suggest which indexes of tabs should be closed to clean up (e.g. duplicate URLs, search pages that are no longer needed, inactive page tabs). "
                f"Return a list of integers corresponding to the tab indexes to close. Format as a JSON list. E.g. [2, 4]"
            )
            router = get_router()
            res = router.generate_text(prompt, task_type="reasoning")
            
            try:
                import re
                match = re.search(r"\[[\d,\s]*\]", res)
                if match:
                    indexes = json.loads(match.group(0))
                    closed = []
                    # Close from highest to lowest index to avoid offset shifting
                    for idx in sorted(indexes, reverse=True):
                        if idx < len(pages):
                            sess.run(pages[idx].close())
                            closed.append(urls[idx])
                    return f"Cleaned up {len(closed)} tabs: {', '.join(closed)}"
            except Exception as e:
                print(f"[AI Browser] Tab cleanup parse error: {e}")
            return "No tabs were closed or cleanup recommendations were not clear."
            
        else:
            return f"Unknown AI Browser action: {action}"
            
    except Exception as e:
        return f"AI Browser failed: {e}"
