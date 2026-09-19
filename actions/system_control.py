import json
import subprocess
import sys
import ctypes
import ctypes.wintypes
import platform
from pathlib import Path

_OS = platform.system()


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# ── helpers ───────────────────────────────────────────────────────────────────

def _ps(cmd: str, timeout: int = 10) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace"
    )
    return (r.stdout + r.stderr).strip()


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _hwnd_by_title(title: str) -> int | None:
    """Return HWND of first visible window whose title contains `title`."""
    title_lower = title.lower()
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def cb(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_lower in buf.value.lower():
                    found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW MANIPULATION
# ══════════════════════════════════════════════════════════════════════════════

def _get_window_info(title: str) -> str:
    hwnd = _hwnd_by_title(title)
    if not hwnd:
        return f"Window not found: {title}"
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right  - rect.left
    h = rect.bottom - rect.top
    return (f"Window: '{title}' | HWND: {hwnd} | "
            f"Pos: ({rect.left},{rect.top}) | Size: {w}×{h}")


def _set_transparency(title: str, alpha: int) -> str:
    """alpha: 0 (invisible) – 255 (opaque)."""
    hwnd = _hwnd_by_title(title)
    if not hwnd:
        return f"Window not found: {title}"
    alpha = max(0, min(255, int(alpha)))
    GWL_EXSTYLE   = -20
    WS_EX_LAYERED = 0x00080000
    LWA_ALPHA     = 0x00000002
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
    return f"Transparency set: '{title}' → alpha {alpha}/255"


def _set_always_on_top(title: str, enable: bool) -> str:
    hwnd = _hwnd_by_title(title)
    if not hwnd:
        return f"Window not found: {title}"
    HWND_TOPMOST    = -1
    HWND_NOTOPMOST  = -2
    SWP_NOMOVE      = 0x0002
    SWP_NOSIZE      = 0x0001
    flag = HWND_TOPMOST if enable else HWND_NOTOPMOST
    ctypes.windll.user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    state = "enabled" if enable else "disabled"
    return f"Always-on-top {state}: '{title}'"


def _tile_windows(direction: str = "horizontal") -> str:
    """Tile all visible top-level windows."""
    if _OS != "Windows":
        return "tile_windows only supported on Windows."
    try:
        import pyautogui
        if direction == "horizontal":
            pyautogui.hotkey("win", "left")
        else:
            pyautogui.hotkey("win", "up")
        return f"Windows tiled ({direction})."
    except Exception as e:
        return f"tile_windows failed: {e}"


def _set_window_opacity_pct(title: str, pct: int) -> str:
    alpha = int(pct / 100 * 255)
    return _set_transparency(title, alpha)


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _resource_usage() -> str:
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.5)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}%  |  "
            f"RAM: {ram.percent}% used ({ram.used // 1024**2} MB / {ram.total // 1024**2} MB)  |  "
            f"Disk: {disk.percent}% used ({disk.used // 1024**3} GB / {disk.total // 1024**3} GB)"
        )
    except ImportError:
        out = _ps(
            "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage;"
            "$m=(Get-CimInstance Win32_OperatingSystem);"
            "Write-Output \"RAM free: $(($m.FreePhysicalMemory/1MB).ToString('F0')) MB\""
        )
        return out or "psutil not installed. Run: pip install psutil"


def _list_processes(top: int = 15) -> str:
    try:
        import psutil
        procs = sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]),
                       key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0,
                       reverse=True)[:top]
        lines = [f"{'PID':>6}  {'CPU%':>5}  {'MEM MB':>7}  Name"]
        for p in procs:
            mem = p.info["memory_info"].rss // 1024**2 if p.info["memory_info"] else 0
            lines.append(f"{p.info['pid']:>6}  {p.info['cpu_percent']:>5.1f}  {mem:>7}  {p.info['name']}")
        return "\n".join(lines)
    except ImportError:
        return _ps(f"Get-Process | Sort-Object WorkingSet64 -Descending | "
                   f"Select-Object -First {top} Id,Name,CPU,"
                   f"@{{n='MemMB';e={{[math]::Round($_.WorkingSet64/1MB,1)}}}} | Format-Table -AutoSize")


def _kill_process(name_or_pid: str) -> str:
    try:
        import psutil
        killed = []
        for p in psutil.process_iter(["pid", "name"]):
            if str(p.info["pid"]) == str(name_or_pid) or \
               name_or_pid.lower() in p.info["name"].lower():
                p.kill()
                killed.append(f"{p.info['name']} ({p.info['pid']})")
        return f"Killed: {', '.join(killed)}" if killed else f"Process not found: {name_or_pid}"
    except ImportError:
        out = _ps(f"Stop-Process -Name '{name_or_pid}' -Force -ErrorAction SilentlyContinue; "
                  f"Write-Output 'done'")
        return f"Kill attempted: {name_or_pid}. {out}"


def _set_process_priority(name_or_pid: str, priority: str) -> str:
    priority_map = {
        "low":         "Idle",
        "below_normal":"BelowNormal",
        "normal":      "Normal",
        "above_normal":"AboveNormal",
        "high":        "High",
        "realtime":    "RealTime",
    }
    ps_priority = priority_map.get(priority.lower(), "Normal")
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if str(p.info["pid"]) == str(name_or_pid) or \
               name_or_pid.lower() in p.info["name"].lower():
                p.nice(getattr(psutil, ps_priority.upper(), psutil.NORMAL_PRIORITY_CLASS))
                return f"Priority set to {priority} for {p.info['name']}"
        return f"Process not found: {name_or_pid}"
    except ImportError:
        out = _ps(f"$p=Get-Process -Name '{name_or_pid}' -ErrorAction SilentlyContinue; "
                  f"if($p){{$p.PriorityClass='{ps_priority}'; Write-Output 'done'}}else{{Write-Output 'not found'}}")
        return out


def _disk_cleanup() -> str:
    if _OS != "Windows":
        return "disk_cleanup only supported on Windows."
    subprocess.Popen(["cleanmgr", "/sagerun:1"])
    return "Disk cleanup launched."


def _empty_recycle_bin() -> str:
    if _OS != "Windows":
        return "empty_recycle_bin only supported on Windows."
    _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
    return "Recycle bin emptied."


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

def _firewall_status() -> str:
    out = _ps(
        "Get-NetFirewallProfile | Select-Object Name,Enabled | Format-Table -AutoSize"
    )
    return out or "Could not read firewall status."


def _firewall_toggle(profile: str, enable: bool) -> str:
    if not _is_admin():
        return "Admin privileges required to change firewall settings."
    state = "True" if enable else "False"
    prof  = profile.title() if profile.lower() in ("domain", "private", "public") else "All"
    cmd   = f"Set-NetFirewallProfile -Profile {prof} -Enabled {state}"
    _ps(cmd)
    return f"Firewall {profile} profile {'enabled' if enable else 'disabled'}."


def _uac_status() -> str:
    out = _ps(
        "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System' "
        "| Select-Object EnableLUA,ConsentPromptBehaviorAdmin | Format-List"
    )
    return out or "Could not read UAC status."


def _defender_status() -> str:
    out = _ps(
        "Get-MpComputerStatus | Select-Object AMServiceEnabled,RealTimeProtectionEnabled,"
        "AntivirusSignatureLastUpdated | Format-List"
    )
    return out or "Windows Defender status unavailable."


def _defender_scan(scan_type: str = "quick") -> str:
    if not _is_admin():
        return "Admin privileges required to run Defender scan."
    st  = "1" if scan_type == "quick" else "2"
    out = _ps(f"Start-MpScan -ScanType {st}")
    return f"Defender {scan_type} scan started. {out}"


def _list_startup_programs() -> str:
    out = _ps(
        "Get-CimInstance Win32_StartupCommand | "
        "Select-Object Name,Command,Location | Format-Table -AutoSize"
    )
    return out or "Could not list startup programs."


def _disable_startup_program(name: str) -> str:
    if not _is_admin():
        return "Admin privileges required to modify startup programs."
    out = _ps(
        f"$item = Get-CimInstance Win32_StartupCommand | Where-Object {{$_.Name -like '*{name}*'}}; "
        f"if($item){{Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' "
        f"-Name $item.Name -ErrorAction SilentlyContinue; Write-Output 'Disabled: ' + $item.Name}}"
        f"else{{Write-Output 'Not found: {name}'}}"
    )
    return out or f"Startup program '{name}' removal attempted."


def _check_windows_update() -> str:
    out = _ps(
        "(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()"
        ".Search('IsInstalled=0').Updates | "
        "Select-Object Title | Format-Table -AutoSize"
    )
    return out if out.strip() else "No pending Windows updates found."


# ══════════════════════════════════════════════════════════════════════════════
# MAIN DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════

def system_control(parameters: dict = None, player=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if player:
        player.write_log(f"[SysCtrl] {action}")
    print(f"[SystemControl] ▶ {action}  {params}")

    # ── WINDOW MANIPULATION ───────────────────────────────────────
    if action == "get_window_info":
        return _get_window_info(params.get("title", ""))

    if action == "set_transparency":
        return _set_transparency(
            params.get("title", ""),
            int(params.get("alpha", params.get("value", 200)))
        )

    if action == "set_opacity":
        return _set_window_opacity_pct(
            params.get("title", ""),
            int(params.get("percent", params.get("value", 80)))
        )

    if action == "set_always_on_top":
        enable = str(params.get("enable", "true")).lower() in ("true", "1", "yes", "on")
        return _set_always_on_top(params.get("title", ""), enable)

    if action == "tile_windows":
        return _tile_windows(params.get("direction", "horizontal"))

    # ── RESOURCE MANAGEMENT ───────────────────────────────────────
    if action in ("resource_usage", "system_stats", "hardware_stats"):
        return _resource_usage()

    if action in ("list_processes", "processes"):
        return _list_processes(int(params.get("top", 15)))

    if action == "kill_process":
        target = params.get("name") or params.get("pid") or params.get("value", "")
        return _kill_process(str(target))

    if action == "set_priority":
        target   = params.get("name") or params.get("pid") or params.get("value", "")
        priority = params.get("priority", "normal")
        return _set_process_priority(str(target), priority)

    if action == "disk_cleanup":
        return _disk_cleanup()

    if action == "empty_recycle_bin":
        return _empty_recycle_bin()

    # ── SECURITY ──────────────────────────────────────────────────
    if action == "firewall_status":
        return _firewall_status()

    if action in ("firewall_enable", "firewall_disable"):
        enable  = action == "firewall_enable"
        profile = params.get("profile", "all")
        return _firewall_toggle(profile, enable)

    if action == "uac_status":
        return _uac_status()

    if action == "defender_status":
        return _defender_status()

    if action == "defender_scan":
        return _defender_scan(params.get("scan_type", "quick"))

    if action == "list_startup":
        return _list_startup_programs()

    if action == "disable_startup":
        return _disable_startup_program(params.get("name", params.get("value", "")))

    if action == "check_updates":
        return _check_windows_update()

    return (
        f"Unknown action: '{action}'. Available:\n"
        "Window: get_window_info | set_transparency | set_opacity | set_always_on_top | tile_windows\n"
        "Resource: resource_usage | list_processes | kill_process | set_priority | disk_cleanup | empty_recycle_bin\n"
        "Security: firewall_status | firewall_enable | firewall_disable | uac_status | "
        "defender_status | defender_scan | list_startup | disable_startup | check_updates"
    )
