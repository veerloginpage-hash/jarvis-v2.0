
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)
from core import local_vision
_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

# Browser actions can also be used outside main.py (tests, background agents).
# Keep diagnostic output from breaking on Windows' legacy console encoding.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _same_site(a: str, b: str) -> bool:
    ha, hb = _host(a), _host(b)
    return bool(ha and hb and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)))


def _is_plain_site_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or ""
    return parsed.scheme in ("http", "https") and path in ("", "/") and not parsed.query

def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = url.strip()
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _clean_search_query(query: str) -> str:
    """Keep only the user's intended search terms, not follow-up instructions."""
    original = str(query or "").strip()
    if not original:
        return ""

    quoted = re.findall(r"[\"'“”‘’](.+?)[\"'“”‘’]", original)
    if quoted:
        return quoted[0].strip()

    text = re.sub(r"\s+", " ", original).strip(" ,.-")
    patterns = [
        r"(?:flipkart|amazon|google|youtube)\s+(?:par|pe|mein|me|on)?\s*(.+?)\s+(?:search|dhund|find)\b",
        r"(.+?)\s+(?:search|dhund|find)\s+(?:karo|karein|karna|maar|marna)\b",
        r"(?:search\s+(?:for\s+)?|search\s+maar(?:o|na)?\s+)(.+?)(?:\s+(?:and|then|aur|phir)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip(" ,.-")
            break

    text = re.sub(
        r"\b(?:aur|and|then|phir)\b.*\b(?:results?|analy[sz]e|compare|scroll|batao|batayein|batana)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:open|kholo|khol|website|flipkart|amazon|par|pe|mein|me|on|karo|karein|karna|please)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if text:
        return text
    if re.search(r"\b(?:karein|karo|analy[sz]e|results?|batayein|batao|phir|aur)\b", original, re.IGNORECASE):
        return ""
    return original


def _clean_search_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    changed = False
    cleaned_pairs = []
    for key, value in pairs:
        if key.lower() in ("q", "query", "search_query", "text", "term"):
            cleaned = _clean_search_query(value)
            changed = changed or cleaned != value
            cleaned_pairs.append((key, cleaned))
        else:
            cleaned_pairs.append((key, value))
    if not changed:
        return url
    return urlunparse(parsed._replace(query=urlencode(cleaned_pairs)))


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
            "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
            "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
            "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                         Path(local) / "Opera Software"   / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                         Path(local) / "Opera Software"   / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Darwin":
        lib = home / "Library" / "Application Support"
        m = {
            "chrome":   [lib / "Google"             / "Chrome"],
            "edge":     [lib / "Microsoft Edge"],
            "brave":    [lib / "BraveSoftware"       / "Brave-Browser"],
            "vivaldi":  [lib / "Vivaldi"],
            "opera":    [lib / "com.operasoftware.Opera"],
            "operagx":  [lib / "com.operasoftware.OperaGX"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Linux":
        cfg = home / ".config"
        m = {
            "chrome":   [cfg / "google-chrome", cfg / "chromium"],
            "edge":     [cfg / "microsoft-edge"],
            "brave":    [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi":  [cfg / "vivaldi"],
            "opera":    [cfg / "opera"],
            "operagx":  [cfg / "opera-gx"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".jarvis_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}

# Avoid repeatedly waiting on a desktop browser profile already in use.
_REAL_PROFILE_BLOCKED: set[str] = set()


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


class _BrowserSession:
    """
    Bir tarayıcı örneği için tam oturum.
    Tüm tarayıcılar launch_persistent_context ile gerçek profil üzerinde açılır.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 30) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    def _remember_page(self, page: Page | None) -> None:
        if page and not page.is_closed():
            self._page = page

    def _best_existing_page(self) -> Page | None:
        if not self._context:
            return None
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            return None
        if self._page in pages:
            return self._page
        for page in reversed(pages):
            if page.url and page.url not in ("about:blank", ""):
                return page
        return pages[-1]

    def _page_for_site(self, url: str) -> Page | None:
        if not self._context:
            return None
        for page in reversed([p for p in self._context.pages if not p.is_closed()]):
            if _same_site(page.url, url):
                return page
        return None

    async def _launch(self):
        """
        Tarayıcıyı gerçek kullanıcı profiliyle başlatır.
        Context zaten açıksa hiçbir şey yapmaz.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".jarvis_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     8_000,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using JARVIS profile")
                jarvis = str(Path.home() / ".jarvis_profiles" / "firefox_jarvis")
                Path(jarvis).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(jarvis, **kwargs)

            self._page = self._best_existing_page() or await self._context.new_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".jarvis_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     8_000,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            self._page = self._best_existing_page() or await self._context.new_page()
            print(f"[Browser] ✅ Safari launched")
            return

        profile = _real_profile_dir(self.browser_name)

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "timeout":     8_000,
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        if self.browser_name not in _REAL_PROFILE_BLOCKED:
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
                self._page = self._best_existing_page() or await self._context.new_page()
                print(f"[Browser] ✅ Launched [{label}] profile={profile}")
                return
            except Exception as e:
                _REAL_PROFILE_BLOCKED.add(self.browser_name)
                print(f"[Browser] ⚠️  Real profile unavailable for {label}: {e}")

        jarvis_profile = str(Path.home() / ".jarvis_profiles" / self.browser_name)
        Path(jarvis_profile).mkdir(parents=True, exist_ok=True)
        print(f"[Browser] Using fast JARVIS profile: {jarvis_profile}")

        try:
            self._context = await engine_obj.launch_persistent_context(jarvis_profile, **kwargs)
            self._page = self._best_existing_page() or await self._context.new_page()
            print(f"[Browser] ✅ Launched [{label}] with JARVIS profile")
        except Exception as e2:
            raise RuntimeError(f"Could not launch {self.browser_name}: {e2}") from e2


    async def _get_page(self) -> Page:
        # If context is dead, reset and relaunch
        if self._context is not None:
            try:
                _ = self._context.pages
            except Exception:
                print("[Browser] Context was closed — resetting for relaunch")
                self._context = None
                self._page = None

        await self._launch()
        self._page = self._best_existing_page()
        if self._page is None or self._page.is_closed():
            try:
                self._page = await self._context.new_page()
            except Exception:
                # Context died between launch and new_page — full reset
                self._context = None
                self._page = None
                await self._launch()
                self._page = await self._context.new_page()
        return self._page

    async def go_to(self, url: str) -> str:

        url      = _normalize_url(url)
        page     = await self._get_page()
        prev_url = page.url

        existing = self._page_for_site(url)
        if existing:
            self._remember_page(existing)
            page = existing
            prev_url = page.url
            if _is_plain_site_url(url):
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                return f"Already on same site: {page.url}"

        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except PlaywrightTimeout:
                pass   # page may have partially loaded — check URL below
            except Exception as e:
                print(f"[Browser] goto exception (non-fatal): {e}")
            return p.url

        result_url = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                new_page   = await self._context.new_page()
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")

        if result_url and result_url not in ("about:blank", "", None):
            return f"Opened: {result_url}"
        return f"Could not open: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        original = str(query or "")
        _engines = {
            "google":     "https://www.google.com/search?q=",
            "bing":       "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "yandex":     "https://yandex.com/search/?text=",
        }
        cleaned = _clean_search_query(query)

        # Site-qualified searches must stay on that site.  Falling through to
        # Google loses the user's intent (e.g. "Flipkart par best laptop").
        site = next((name for name in ("flipkart", "amazon", "youtube")
                     if name in original.lower()), None)
        if site is None:
            current_page = self._best_existing_page()
            current_url = current_page.url.lower() if current_page else ""
            site = next((name for name in ("flipkart", "amazon", "youtube")
                         if name in current_url), None)
        if site:
            targets = {
                "flipkart": ("https://www.flipkart.com", "/search?q="),
                "amazon": ("https://www.amazon.in", "/s?k="),
                "youtube": ("https://www.youtube.com", "/results?search_query="),
            }
            root, path = targets[site]
            page = await self._get_page()
            if not _same_site(page.url, root):
                await self.go_to(root)
                page = await self._get_page()

            # Prefer the real search box so cookies, locale and current tab
            # state are preserved; use the site's URL only as a fast fallback.
            selectors = (
                'input[placeholder*="Search"]',
                'input[placeholder*="search"]',
                'input[name="q"]',
                'input[type="search"]',
            )
            for selector in selectors:
                try:
                    field = page.locator(selector).first
                    if await field.count():
                        await field.fill(cleaned)
                        await field.press("Enter")
                        await page.wait_for_timeout(75)
                        current_url = page.url.lower()
                        if ("search" in current_url or "/s?" in current_url
                                or quote_plus(cleaned).lower() in current_url):
                            return f"Searched {site.title()} for '{cleaned}' — {page.url}"
                        break
                except Exception:
                    continue
            result = await self.go_to(root + path + quote_plus(cleaned))
            if _same_site(page.url, root):
                return result

        base = _engines.get(engine.lower(), _engines["google"])
        return await self.go_to(base + quote_plus(cleaned))

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked text: '{text}'"
            if selector:
                await page.click(selector, timeout=8_000)
                return f"Clicked selector: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found (timeout)."
        except Exception as e:
            return f"Click error: {e}"

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await el.clear()
            await el.type(text, delay=0)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def upload_file(self, path: str, selector: str = None) -> str:
        """Upload a local file through a page file input, on any platform."""
        file_path = str(Path(path).expanduser().resolve())
        if not Path(file_path).is_file():
            return f"Could not upload: file not found at {file_path}"
        page = await self._get_page()
        try:
            locator = page.locator(selector).first if selector else page.locator('input[type="file"]').first
            if not await locator.count():
                return "Could not upload: no file input found on the current page."
            await locator.set_input_files(file_path)
            await page.wait_for_timeout(75)
            return f"File selected for upload: {Path(file_path).name}"
        except Exception as e:
            return f"Upload error: {e}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            # Smooth, bounded scroll feels natural without adding long sleeps.
            await page.evaluate(
                """(dy) => window.scrollBy({top: dy, left: 0, behavior: 'smooth'})""",
                y,
            )
            await page.wait_for_timeout(min(180, max(60, abs(y) // 5)))
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            raw_key = str(key or "Enter").strip()
            aliases = {
                "enter": "Enter", "return": "Enter", "esc": "Escape",
                "escape": "Escape", "tab": "Tab", "backspace": "Backspace",
                "delete": "Delete", "space": "Space", "up": "ArrowUp",
                "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
            }
            normalized = aliases.get(raw_key.lower(), raw_key)
            await page.keyboard.press(normalized)
            return f"Pressed: {normalized}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def get_state(self) -> str:
        page = await self._get_page()
        try:
            focused_value = await page.evaluate(
                """() => {
                    const el = document.activeElement;
                    if (!el) return "";
                    if ("value" in el) return String(el.value || "");
                    return String(el.textContent || "").slice(0, 200);
                }"""
            )
        except Exception:
            focused_value = ""
        try:
            scroll_y = await page.evaluate("() => Math.round(window.scrollY || 0)")
        except Exception:
            scroll_y = 0
        try:
            title = await page.title()
        except Exception:
            title = ""
        pages = []
        if self._context:
            for p in self._context.pages:
                if not p.is_closed():
                    pages.append(p.url)
        return json.dumps(
            {
                "browser": self.browser_name,
                "url": page.url,
                "title": title,
                "scroll_y": scroll_y,
                "focused_value": focused_value[:500],
                "tabs": pages,
            },
            ensure_ascii=True,
        )

    async def click_at(self, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        """Click viewport coordinates from the browser session's own event loop."""
        page = await self._get_page()
        try:
            await page.mouse.move(int(x), int(y))
            await page.mouse.click(int(x), int(y), button=button, click_count=max(1, int(clicks)))
            return f"Clicked coordinates ({int(x)}, {int(y)})"
        except Exception as exc:
            return f"Coordinate click failed: {exc}"

    async def type_at(self, x: int, y: int, text: str, clear_first: bool = True) -> str:
        """Focus a coordinate target and type on the browser event loop."""
        page = await self._get_page()
        try:
            await page.mouse.click(int(x), int(y))
            if clear_first:
                await page.keyboard.press("ControlOrMeta+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(str(text), delay=0)
            return "Typed into coordinate target"
        except Exception as exc:
            return f"Coordinate typing failed: {exc}"

    async def fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=0)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def smart_click(self, description: str, timeout: int = 1200) -> str:
        """
        4-strategy cascade click:
        1. Playwright role-based (fast, case-insensitive)
        2. CSS selector cascade (links, buttons, nav items, categories)
        3. JavaScript full DOM fuzzy scan with scoring → precise mouse.click()
        4. Force-click fallback on broadest match
        """
        page = await self._get_page()
        # Keep the cascade responsive: a missing candidate should fail fast,
        # while successful role/selector matches still return immediately.
        timeout = max(300, min(int(timeout), 1800))
        desc = description.strip()
        if not desc:
            return "smart_click requires a description"

        # Fast ordinal intent: “first video”, “second result”, “last link”.
        # This is more reliable than asking a text matcher to find words that
        # do not exist on the card itself.
        ordinal_match = re.search(
            r"\b(first|1st|second|2nd|third|3rd|fourth|4th|last)\b", desc.lower()
        )
        if ordinal_match and any(
            word in desc.lower() for word in ("video", "result", "link", "item", "post", "card")
        ):
            ordinal = {
                "first": 0, "1st": 0, "second": 1, "2nd": 1,
                "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
                "last": -1,
            }[ordinal_match.group(1)]
            try:
                coords = await page.evaluate(
                    """(kind, ordinal) => {
                        const visible = (el) => {
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 20 && r.height > 20 && r.bottom > 0 &&
                                r.top < innerHeight && s.display !== 'none' &&
                                s.visibility !== 'hidden';
                        };
                        const all = [...document.querySelectorAll(
                            'a[href], article, [role="article"], ytd-video-renderer,' +
                            'ytd-grid-video-renderer, ytd-rich-item-renderer, [data-testid]'
                        )].filter(visible);
                        const isVideo = (el) => {
                            const a = el.matches('a[href]') ? el : el.querySelector('a[href]');
                            const href = a?.getAttribute('href') || '';
                            const txt = (el.innerText || '').toLowerCase();
                            return /watch\\?|video|shorts\\//.test(href) ||
                                el.tagName.toLowerCase().includes('video') ||
                                (kind.includes('video') && txt.length > 8);
                        };
                        const isLink = (el) => el.matches('a[href]') || !!el.querySelector('a[href]');
                        let items = all.filter(el => kind.includes('video') ? isVideo(el) : isLink(el));
                        // Remove nested duplicates so one card counts once.
                        items = items.filter((el, i) => !items.some((other, j) =>
                            j < i && other !== el && other.contains(el)));
                        if (!items.length) return null;
                        const el = ordinal < 0 ? items[items.length - 1] : items[ordinal];
                        if (!el) return null;
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        const r = el.getBoundingClientRect();
                        return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
                    }""",
                    desc.lower(), ordinal,
                )
                if coords:
                    await page.mouse.click(coords["x"], coords["y"])
                    return f"Clicked {ordinal_match.group(1)} target: '{description}'"
            except Exception as ordinal_err:
                print(f"[Browser] Ordinal resolver fallback: {ordinal_err}")

        # ── Strategy 1: Playwright role-based (fastest) ──────────────────────
        for role in ("link", "button", "menuitem", "tab", "option", "checkbox", "radio"):
            try:
                loc = page.get_by_role(role, name=desc, exact=False)
                if await loc.count() > 0:
                    el = loc.first
                    await el.scroll_into_view_if_needed()
                    await el.click(timeout=timeout)
                    return f"Clicked ({role}): '{description}'"
            except Exception:
                pass

        # ── Strategy 2: CSS selector cascade ─────────────────────────────────
        selectors = [
            f'a:has-text("{desc}")',
            f'button:has-text("{desc}")',
            f'[role="button"]:has-text("{desc}")',
            f'[role="menuitem"]:has-text("{desc}")',
            f'[role="tab"]:has-text("{desc}")',
            f'[role="link"]:has-text("{desc}")',
            f'nav a:has-text("{desc}")',
            f'[class*="nav"] a:has-text("{desc}")',
            f'[class*="menu"] a:has-text("{desc}")',
            f'[class*="categor"] a:has-text("{desc}")',
            f'[class*="categor"]:has-text("{desc}")',
            f'header a:has-text("{desc}")',
            f'[aria-label*="{desc}" i]',
            f'[title*="{desc}" i]',
            f'[alt*="{desc}" i]',
            f'input[type="submit"][value*="{desc}" i]',
            f'input[type="button"][value*="{desc}" i]',
            f'span:has-text("{desc}")',
            f'li > a:has-text("{desc}")',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    el = loc.first
                    await el.scroll_into_view_if_needed()
                    await el.click(timeout=timeout)
                    return f"Clicked element: '{description}'"
            except Exception:
                pass

        # ── Strategy 3: JavaScript full DOM fuzzy scan (no API, fast) ────────
        try:
            coords = await page.evaluate("""
                (desc) => {
                    const descLower = desc.toLowerCase();
                    const tags = 'a,button,[role="button"],[role="menuitem"],[role="tab"],[role="link"],span,li,div,td,label,input[type="button"],input[type="submit"],svg,img,[aria-label],[title],[data-tooltip]';
                    const allEls = document.querySelectorAll(tags);
                         let bestEl = null;
                    let bestScore = 0;

                         for (const el of allEls) {
                        const rect = el.getBoundingClientRect();
                        // Skip invisible / off-screen / tiny elements
                        if (rect.width < 3 || rect.height < 3) continue;
                        if (rect.top < -200 || rect.top > window.innerHeight + 200) continue;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (parseFloat(style.opacity) < 0.05) continue;

                        const rawText = (el.innerText || el.textContent || '').trim();
                         const parent = el.closest('a,button,[role="button"],[role="menuitem"],[role="tab"],[role="link"]');
                        const attrText = [
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('title') || '',
                            el.getAttribute('alt') || '',
                            el.getAttribute('value') || '',
                            el.getAttribute('placeholder') || '',
                            el.getAttribute('data-tooltip') || '',
                            parent ? (parent.innerText || parent.getAttribute('aria-label') || '') : '',
                        ].filter(Boolean).join(' ');
                        const text = (rawText || attrText).trim();
                        const textLower = text.toLowerCase();

                        if (!textLower) continue;

                        let score = 0;
                        if (textLower === descLower) score = 100;
                        else if (textLower.startsWith(descLower)) score = 85;
                        else if (textLower.includes(descLower)) score = 65;
                        else if (descLower.includes(textLower) && textLower.length > 2) score = 45;

                        // Bonus for interactive / nav elements
                        if (score > 0) {
                            const tag = el.tagName;
                            if (tag === 'A' || tag === 'BUTTON' || parent) score += 15;
                            if (el.getAttribute('role') === 'button' || el.getAttribute('role') === 'link') score += 10;
                            if (el.closest('nav, header, [class*="navbar"], [class*="menu"]')) score += 8;
                            // Prefer shorter text (more specific match)
                            if (text.length <= desc.length + 5) score += 5;
                        }

                         // Text often lives in a span inside the real control. Always
                         // return the actionable ancestor so the click lands on the
                         // control, not on a decorative/text-only child.
                         const target = parent || el;
                         if (score > bestScore) {
                             bestScore = score;
                             bestEl = target;
                         }
                    }

                    if (bestEl && bestScore >= 40) {
                        bestEl.scrollIntoView({behavior: 'instant', block: 'center'});
                        const r = bestEl.getBoundingClientRect();
                        return {
                            x: Math.round(r.left + r.width / 2),
                            y: Math.round(r.top + r.height / 2),
                            score: bestScore,
                            tag: bestEl.tagName
                        };
                    }
                    return null;
                }
            """, desc)

            if coords:
                await page.mouse.move(coords['x'], coords['y'])
                await page.mouse.click(coords['x'], coords['y'])
                return (
                    f"JS-found & clicked '{description}' "
                    f"at ({coords['x']}, {coords['y']}) "
                    f"[{coords['tag']}, score={coords['score']}]"
                )
        except Exception as js_err:
            print(f"[Browser] JS DOM scan error: {js_err}")

        # ── Strategy 4: Force-click broadest match ────────────────────────────
        try:
            loc = page.locator(f"text=/{desc}/i").first
            count = await loc.count()
            if count > 0:
                await loc.scroll_into_view_if_needed()
                await loc.click(timeout=timeout, force=True)
                return f"Force-clicked text match: '{description}'"
        except Exception:
            pass

        # Strategy 5: local vision fallback. This is deliberately last so
        # normal DOM actions stay instant, while canvas/custom UIs still work.
        try:
            image = await page.screenshot(type="jpeg", quality=65, full_page=False)
            prompt = (
                "Find the visible clickable UI element described below. "
                "Reply with ONLY x,y or NOT_FOUND. Viewport coordinates are pixels.\n"
                f"Element: {description}"
            )
            vision_text = await asyncio.to_thread(local_vision.analyze, image, prompt, 40)
            match = re.search(r"(\d+)\s*,\s*(\d+)", vision_text or "")
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                await page.mouse.click(x, y)
                return f"Vision-found & clicked '{description}' at ({x}, {y})"
        except Exception as vision_err:
            print(f"[Browser] Local vision click fallback unavailable: {vision_err}")

        return f"Could not find element: '{description}'"

    async def smart_type(self, description: str, text: str) -> str:
        """
        Improved input finder:
        1. Playwright role/placeholder/label
        2. JavaScript input field scan
        3. Focused element fallback
        """
        page = await self._get_page()
        desc_lower = description.lower()

        # Strategy 1: Playwright built-in matchers
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role_text",   page.get_by_role("textbox", name=description, exact=False)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        if any(word in desc_lower for word in ("search", "query", "find", "lookup")):
            candidates.extend([
                ("search_input", page.locator('input[type="search"]:visible').first),
                ("search_aria", page.locator('input[aria-label*="search" i]:visible').first),
                ("search_placeholder", page.locator('input[placeholder*="search" i]:visible').first),
                ("search_name", page.locator('input[name*="search" i]:visible, input[name="q"]:visible').first),
            ])
        for method, loc in candidates:
            try:
                if await loc.count() == 0:
                    continue
                el = loc.first
                await el.scroll_into_view_if_needed()
                await el.click(timeout=700)
                await el.clear()
                await el.type(text, delay=0)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue

        # Strategy 2: JavaScript full input field scan
        try:
            coords = await page.evaluate("""
                (desc) => {
                    const descLower = desc.toLowerCase();
                    const inputs = document.querySelectorAll(
                        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]),'
                        + 'textarea, [contenteditable="true"]'
                    );
                    let bestEl = null;
                    let bestScore = 0;

                    for (const el of inputs) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 10 || rect.height < 5) continue;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;

                        const attrs = [
                            el.getAttribute('placeholder') || '',
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('name') || '',
                            el.getAttribute('id') || '',
                            el.getAttribute('title') || '',
                        ].join(' ').toLowerCase();

                        // Try to find associated label
                        let labelText = '';
                        if (el.id) {
                            const lbl = document.querySelector('label[for="' + el.id + '"]');
                            if (lbl) labelText = (lbl.innerText || '').toLowerCase();
                        }

                        const combined = attrs + ' ' + labelText;
                        let score = 0;
                        if (combined.includes(descLower)) score = 70;
                        else if (descLower.split(' ').some(w => w.length > 2 && combined.includes(w))) score = 40;

                        // Bonus: visible focused or first input on page
                        if (score === 0 && inputs.length === 1) score = 30;

                        if (score > bestScore) {
                            bestScore = score;
                            bestEl = el;
                        }
                    }

                    if (bestEl && bestScore >= 30) {
                        bestEl.scrollIntoView({behavior: 'instant', block: 'center'});
                        const r = bestEl.getBoundingClientRect();
                        return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                    }
                    return null;
                }
            """, description)

            if coords:
                await page.mouse.click(coords['x'], coords['y'])
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.keyboard.type(text, delay=0)
                return f"JS-found input at ({coords['x']},{coords['y']}) and typed: '{text}'"
        except Exception as e:
            print(f"[Browser] JS input scan error: {e}")

        # Strategy 3: Use the focused element only when it is actually editable.
        # Blindly typing into the page used to report success even when nothing
        # received the text.
        try:
            editable = await page.evaluate("""() => {
                const el = document.activeElement;
                if (!el) return false;
                const tag = el.tagName.toLowerCase();
                return ['input', 'textarea'].includes(tag) || el.isContentEditable;
            }""")
            if not editable:
                raise RuntimeError("no editable element is focused")
            await page.keyboard.press("ControlOrMeta+A")
            await page.keyboard.type(text, delay=0)
            return f"Typed into focused element: '{text}'"
        except Exception:
            pass

        # Strategy 4: local vision for canvas/custom input controls.
        try:
            image = await page.screenshot(type="jpeg", quality=65, full_page=False)
            prompt = (
                "Find the visible input field described below. Reply ONLY x,y or NOT_FOUND. "
                f"Input: {description}"
            )
            vision_text = await asyncio.to_thread(local_vision.analyze, image, prompt, 40)
            match = re.search(r"(\d+)\s*,\s*(\d+)", vision_text or "")
            if match:
                await page.mouse.click(int(match.group(1)), int(match.group(2)))
                await page.keyboard.press("Control+a")
                await page.keyboard.type(text, delay=0)
                return f"Vision-found input and typed into '{description}'"
        except Exception as vision_err:
            print(f"[Browser] Local vision type fallback unavailable: {vision_err}")

        return f"Could not find input: '{description}'"

    async def in_page_navigate(self, target: str) -> str:
        """
        Navigate WITHIN the currently open page/site — click on a nav link,
        category, menu item, or any visible text link without leaving the site.
        Example: Flipkart open hai → 'Fashion' category pe jaana.
        Uses smart_click with nav-biased scoring.
        """
        page = await self._get_page()
        desc = target.strip()
        current_url = page.url

        # Try smart_click first (it handles nav/category elements with bonus scoring)
        result = await self.smart_click(desc)
        if "Could not find" not in result:
            new_url = page.url
            if new_url != current_url:
                return f"Navigated to '{desc}' → {new_url}"
            return f"Clicked '{desc}' on current page"

        # Fallback: look specifically in nav / header / sidebar elements
        try:
            coords = await page.evaluate("""
                (desc) => {
                    const descLower = desc.toLowerCase();
                    // Focus specifically on navigation areas
                    const navAreas = document.querySelectorAll(
                        'nav, header, [class*="nav"], [class*="sidebar"], [class*="menu"], '
                        + '[class*="categor"], [class*="tab"], aside, [role="navigation"]'
                    );
                    let bestEl = null;
                    let bestScore = 0;

                    const searchIn = navAreas.length > 0
                        ? navAreas
                        : [document.body]; // fallback to full body

                    for (const area of searchIn) {
                        const links = area.querySelectorAll('a, button, span, li, div');
                        for (const el of links) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width < 3 || rect.height < 3) continue;
                            if (rect.top < 0 || rect.top > window.innerHeight) continue;
                            const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                            if (!text) continue;

                            let score = 0;
                            if (text === descLower) score = 100;
                            else if (text.startsWith(descLower)) score = 80;
                            else if (text.includes(descLower)) score = 60;

                            if (score > bestScore) { bestScore = score; bestEl = el; }
                        }
                    }

                    if (bestEl && bestScore >= 50) {
                        bestEl.scrollIntoView({behavior: 'instant', block: 'center'});
                        const r = bestEl.getBoundingClientRect();
                        return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2), score: bestScore};
                    }
                    return null;
                }
            """, desc)

            if coords:
                await page.mouse.move(coords['x'], coords['y'])
                await page.mouse.click(coords['x'], coords['y'])
                return f"Nav-clicked '{desc}' at ({coords['x']},{coords['y']}) → {page.url}"
        except Exception as e:
            print(f"[Browser] in_page_navigate JS error: {e}")

        return f"Could not navigate to '{target}' on current page ({current_url})"

    async def new_tab(self, url: str = "") -> str:
        page = await self._get_page()
        ctx  = page.context
        if url:
            normalized = _normalize_url(url)
            existing = self._page_for_site(normalized)
            if existing:
                self._remember_page(existing)
                try:
                    await existing.bring_to_front()
                except Exception:
                    pass
                return f"Reused existing tab: {existing.url}"
        new  = await ctx.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Navigated back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Navigated forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Tüm aktif tarayıcı oturumlarını yönetir."""

    def __init__(self):
        self._sessions:       dict[str, _BrowserSession] = {}
        self._active_browser: str                        = ""
        self._lock            = threading.Lock()

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        if action == "go_to":
            result = sess.run(sess.go_to(_clean_search_url(params.get("url", ""))))
        elif action == "search":
            result = sess.run(sess.search(params.get("query", ""), params.get("engine", "google")))
        elif action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "upload_file":
            result = sess.run(sess.upload_file(
                params.get("path", ""), params.get("selector")))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "in_page_navigate":
            result = sess.run(sess.in_page_navigate(params.get("target", params.get("description", ""))))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "get_state":
            result = sess.run(sess.get_state())
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "new_tab":
            result = sess.run(sess.new_tab(params.get("url", "")))
        elif action == "close_tab":
            result = sess.run(sess.close_tab())
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action == "back":
            result = sess.run(sess.back())
        elif action == "forward":
            result = sess.run(sess.forward())
        elif action == "reload":
            result = sess.run(sess.reload())
        elif action == "close":
            target = browser or _registry._active_browser
            result = _registry.close_one(target) if target else "No browser specified."
        else:
            result = f"Unknown browser action: '{action}'"

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (30s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        player.write_log(f"[browser] {short[:60]}")
