from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, QEvent, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut, QConicalGradient,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar, QGraphicsDropShadowEffect,
)

# Used for animated GIF rendering (Qt supports animated GIFs via QMovie)
from PyQt6.QtGui import QMovie

import urllib.request


# ────────────────── paths ──────────────────

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 1320, 780
_MIN_W,     _MIN_H     = 1024, 620
_LEFT_W  = 260
_RIGHT_W = 310

_OS = platform.system()

# ────────────────── colours ──────────────────

class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    PANEL3    = "#081a28"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"
    HEADER_BG = "#040e18"

def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

# ────────────────── system metrics (unchanged) ──────────────────

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0
        self.gpu  = -1.0
        self.tmp  = -1.0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()
        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass
        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }

_metrics = _SysMetrics()

# ────────────────── background widget ──────────────────

class _BgWidget(QWidget):
    """Central widget that paints the background image."""
    def __init__(self, parent=None):
        super().__init__(parent)
        bg_file = BASE_DIR / "background.png"
        self._bg_px = QPixmap(str(bg_file)) if bg_file.exists() else None
        if self._bg_px and self._bg_px.isNull():
            self._bg_px = None

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = self.width(), self.height()
        if self._bg_px:
            sc = self._bg_px.scaled(W, H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((W - sc.width()) // 2, (H - sc.height()) // 2, sc)
        else:
            p.fillRect(self.rect(), qcol("#050a0e"))
        # Dim overlay
        p.fillRect(self.rect(), qcol("#000000", 60))

# ────────────────── floating panel ──────────────────

class _FloatingPanel(QWidget):
    """Semi-transparent panel with cyan glow border."""
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        # Fill
        p.setBrush(QBrush(qcol("#070e18", 215)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 6, 6)
        # Glow layers
        for i in range(5):
            p.setPen(QPen(qcol(C.PRI, 18 - i * 3), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-i, -i, i, i), 6 + i, 6 + i)
        # Main border
        p.setPen(QPen(qcol(C.PRI, 90), 1.2))
        p.drawRoundedRect(r, 6, 6)
        # Bottom highlight
        p.setPen(QPen(qcol(C.PRI, 140), 1.5))
        p.drawLine(QPointF(16, self.height() - 3),
                   QPointF(self.width() - 16, self.height() - 3))

# ────────────────── enhanced metric bar ──────────────────

class MetricBar(QWidget):
    """Single system metric row with icon, value, status, and circular gauge."""
    def __init__(self, label: str, color: str = C.PRI, icon: str = "⚡", parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._icon  = icon
        self._value = 0.0
        self._text  = "--"
        self.setFixedHeight(46)
        self.setMinimumWidth(100)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def _status(self) -> tuple[str, str]:
        v = self._value
        if v < 30:   return "Optimal",   C.GREEN
        if v < 60:   return "Optimised", C.PRI
        if v < 80:   return "Stable",    C.ACC2
        if v < 92:   return "Moderate",  C.ACC
        return "Critical", C.RED

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # background
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER, 100), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 5, 5)

        # icon
        icon_font = QFont("Segoe UI Emoji", 13) if _OS == "Windows" else QFont("Arial", 13)
        p.setFont(icon_font)
        p.setPen(QPen(qcol(self._color), 1))
        p.drawText(QRectF(6, 0, 28, H), Qt.AlignmentFlag.AlignCenter, self._icon)

        # label: value
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(QRectF(38, 5, W - 90, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._label}: {self._text}")

        # status text
        status_txt, status_col = self._status()
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(status_col), 1))
        p.drawText(QRectF(38, 22, W - 90, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   status_txt)

        # circular gauge
        gr = 13
        gcx, gcy = W - 26, H // 2
        rect_g = QRectF(gcx - gr, gcy - gr, gr * 2, gr * 2)
        p.setPen(QPen(qcol(C.BAR_BG), 3.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect_g)
        span = int(self._value * 360 / 100)
        arc_c = qcol(C.RED) if self._value > 85 else (qcol(C.ACC) if self._value > 65 else qcol(self._color))
        p.setPen(QPen(arc_c, 3.5, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(rect_g, 90 * 16, -span * 16)
        p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(rect_g, Qt.AlignmentFlag.AlignCenter, f"{self._value:.0f}%")

# ────────────────── environment widget ──────────────────

class EnvironmentWidget(QWidget):
    """Three circular icon buttons: Climate, Security, Lighting."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(110)
        self._items = [
            ("❄", "Climate",  "#00aaff", "#002244"),
            ("🛡", "Security", "#ffcc00", "#221a00"),
            ("💡", "Lighting", "#ff6b00", "#221200"),
        ]
        self._hover = -1
        self.setMouseTracking(True)

    def mouseMoveEvent(self, e):
        W = self.width()
        cols = 3
        cw = W // cols
        idx = min(int(e.pos().x() / cw), cols - 1)
        if 0 <= idx < len(self._items) and self._hover != idx:
            self._hover = idx
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Header
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, 2, W, 16), Qt.AlignmentFlag.AlignLeft, "  ENVIRONMENT")
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 16, W, 12), Qt.AlignmentFlag.AlignLeft, "  Internal climate, Security, Lighting")

        cw = W // 3
        cy = 58
        cr = 20

        for i, (icon, label, col, bg_col) in enumerate(self._items):
            cx = cw * i + cw // 2
            hovered = (i == self._hover)
            # glow
            if hovered:
                for g in range(6):
                    p.setPen(QPen(qcol(col, 20 - g * 3), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(cx, cy), cr + g + 2, cr + g + 2)
            # circle fill
            p.setBrush(QBrush(qcol(bg_col if not hovered else col, 60 if hovered else 30)))
            p.setPen(QPen(qcol(col, 200 if hovered else 120), 1.5))
            p.drawEllipse(QPointF(cx, cy), cr, cr)
            # icon
            icon_font = QFont("Segoe UI Emoji", 12) if _OS == "Windows" else QFont("Arial", 12)
            p.setFont(icon_font)
            p.setPen(QPen(qcol(col), 1))
            p.drawText(QRectF(cx - cr, cy - cr, cr * 2, cr * 2), Qt.AlignmentFlag.AlignCenter, icon)
            # label
            p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            p.setPen(QPen(qcol(col if hovered else C.TEXT_DIM), 1))
            p.drawText(QRectF(cx - 35, cy + cr + 4, 70, 12), Qt.AlignmentFlag.AlignCenter, label)

# ────────────────── radar chart ──────────────────

class RadarChartWidget(QWidget):
    """Spider/radar chart for system overview metrics."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._labels = ["CPU\nControl", "Besy\nReshelled", "Data\nLighting", "High Gearage\nAnalytics"]
        self._values = [0.5, 0.6, 0.4, 0.7]
        self._tick   = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(80)

    def _step(self):
        self._tick += 1
        self.update()

    def set_values(self, vals: list[float]):
        self._values = [max(0, min(1, v)) for v in vals]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W // 2, H // 2 + 4
        R = min(W, H) // 2 - 28
        n = len(self._labels)

        # grid rings
        for ring in range(1, 6):
            r = R * ring / 5
            p.setPen(QPen(qcol(C.BORDER, 60 + ring * 10), 0.7))
            p.setBrush(Qt.BrushStyle.NoBrush)
            pts = []
            for i in range(n):
                a = math.radians(90 - i * 360 / n)
                pts.append(QPointF(cx + r * math.cos(a), cy - r * math.sin(a)))
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            path.closeSubpath()
            p.drawPath(path)

        # axes
        p.setPen(QPen(qcol(C.BORDER_A, 100), 0.7))
        for i in range(n):
            a = math.radians(90 - i * 360 / n)
            p.drawLine(QPointF(cx, cy),
                       QPointF(cx + R * math.cos(a), cy - R * math.sin(a)))

        # value polygon with pulse
        pulse = 1.0 + 0.03 * math.sin(self._tick * 0.12)
        pts = []
        for i in range(n):
            a = math.radians(90 - i * 360 / n)
            v = self._values[i] * pulse
            r_v = R * min(1, v)
            pts.append(QPointF(cx + r_v * math.cos(a), cy - r_v * math.sin(a)))
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        path.closeSubpath()
        p.setBrush(QBrush(qcol(C.PRI, 35)))
        p.setPen(QPen(qcol(C.PRI, 180), 1.5))
        p.drawPath(path)
        # dots
        for pt in pts:
            p.setBrush(QBrush(qcol(C.PRI)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pt, 3, 3)

        # labels
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        for i, lbl in enumerate(self._labels):
            a = math.radians(90 - i * 360 / n)
            lx = cx + (R + 18) * math.cos(a)
            ly = cy - (R + 18) * math.sin(a)
            flags = Qt.AlignmentFlag.AlignCenter
            p.drawText(QRectF(lx - 40, ly - 12, 80, 24), flags, lbl)

# ────────────────── HUD canvas (enhanced) ──────────────────

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):

        super().__init__(parent)
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.show_centerpiece = True
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)
        self._rainbow_bars: list[float] = [0.0] * 32

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(33)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        # rainbow bars
        for i in range(len(self._rainbow_bars)):
            if self.speaking:
                self._rainbow_bars[i] += (random.uniform(0.3, 1.0) - self._rainbow_bars[i]) * 0.35
            else:
                tgt = 0.15 + 0.12 * math.sin(self._tick * 0.06 + i * 0.4)
                self._rainbow_bars[i] += (tgt - self._rainbow_bars[i]) * 0.15

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG, 0))  # transparent

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2 - 20
        fw = min(W, H - 40)

        # grid dots
        p.setPen(QPen(qcol(C.PRI_GHO, 60), 1))
        for x in range(0, W, 50):
            for y in range(0, H, 50):
                p.drawPoint(x, y)

        # Live HUD telemetry labels. Keep these lightweight and readable so
        # the centre remains useful even when the face/GIF is active.
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.GREEN, 190), 1))
        p.drawText(QRectF(18, 16, 170, 16), Qt.AlignmentFlag.AlignLeft,
                   "● NEURAL CORE  ONLINE")
        p.setPen(QPen(qcol(C.TEXT_DIM, 180), 1))
        p.setFont(QFont("Courier New", 6))
        p.drawText(QRectF(18, 34, 200, 14), Qt.AlignmentFlag.AlignLeft,
                   "VISION LINK  //  READY")
        p.drawText(QRectF(W - 220, 16, 202, 16), Qt.AlignmentFlag.AlignRight,
                   "VEER INDUS  //  LOCAL MODE")
        p.drawText(QRectF(W - 220, 34, 202, 14), Qt.AlignmentFlag.AlignRight,
                   f"T+{self._tick // 30:05d}  //  SECURE CHANNEL")

        r_face = fw * 0.28

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.07 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(180 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings — outer decorative rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.46, 3, 100, 65), (0.39, 2, 70, 50), (0.32, 1.5, 50, 38)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # outer ring with detailed tick marks
        t_out, t_in = fw * 0.475, fw * 0.455
        for deg in range(0, 360, 5):
            a_t = max(0, min(255, int(self._halo * 0.7)))
            p.setPen(QPen(qcol(C.PRI, a_t), 1 if deg % 30 != 0 else 1.5))
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            if deg % 15 == 0:
                inn = t_in + 2
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # degree labels at cardinal points
        p.setFont(QFont("Courier New", 6))
        deg_r = fw * 0.49
        for deg, lbl in [(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")]:
            rad = math.radians(deg)
            lx = cx + deg_r * math.cos(rad)
            ly = cy - deg_r * math.sin(rad)
            p.setPen(QPen(qcol(C.TEXT_DIM, 150), 1))
            p.drawText(QRectF(lx - 14, ly - 7, 28, 14), Qt.AlignmentFlag.AlignCenter, lbl)

        # scanners
        sr = fw * 0.47
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # crosshair
        ch_r, gap_h = fw * 0.50, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.4)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 22
        bc = qcol(C.PRI, 180)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # centre content — face or JARVIS text
        if self.show_centerpiece:
            if self._face_px:
                fsz = int(fw * 0.52 * self._scale)
                scaled = self._face_px.scaled(
                    fsz, fsz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
            else:
                # JARVIS text orb
                orb_r = int(fw * 0.22 * self._scale)
                oc = (180, 0, 40) if self.muted else (0, 45, 85)
                for i in range(8, 0, -1):
                    r2  = int(orb_r * i / 8)
                    frc = i / 8
                    a   = max(0, min(255, int(self._halo * 0.9 * frc)))
                    p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))

                # JARVIS title
                p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2.5))), 1))
                p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
                p.drawText(QRectF(cx - 100, cy - 20, 200, 28),
                           Qt.AlignmentFlag.AlignCenter, "J.A.R.V.I.S.")

                # subtitle
                p.setFont(QFont("Courier New", 8))
                p.setPen(QPen(qcol(C.PRI_DIM, min(255, int(self._halo * 2))), 1))
                p.drawText(QRectF(cx - 80, cy + 8, 160, 16),
                           Qt.AlignmentFlag.AlignCenter, "Core Interface")
        else:
            # JARVIS text orb
            orb_r = int(fw * 0.22 * self._scale)
            oc = (180, 0, 40) if self.muted else (0, 45, 85)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                a   = max(0, min(255, int(self._halo * 0.9 * frc)))
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))

            # JARVIS title
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2.5))), 1))
            p.setFont(QFont("Courier New", 16, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 100, cy - 20, 200, 28),
                       Qt.AlignmentFlag.AlignCenter, "J.A.R.V.I.S.")

            # subtitle
            p.setFont(QFont("Courier New", 8))
            p.setPen(QPen(qcol(C.PRI_DIM, min(255, int(self._halo * 2))), 1))
            p.drawText(QRectF(cx - 80, cy + 8, 160, 16),
                       Qt.AlignmentFlag.AlignCenter, "Core Interface")


        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # ── rainbow frequency visualizer arc ──
        rb_cy = cy + fw * 0.36
        n_bars = len(self._rainbow_bars)
        bar_w = 6
        total_w = n_bars * bar_w
        rb_x0 = cx - total_w / 2
        rainbow = ["#ff6b00", "#ff3355", "#ffcc00", "#00ff88", "#00d4ff",
                    "#4488ff", "#cc44ff", "#ff6b00"]
        for i in range(n_bars):
            t = i / max(n_bars - 1, 1)
            ci = int(t * (len(rainbow) - 1))
            ci = min(ci, len(rainbow) - 2)
            bar_col = rainbow[ci]
            h = max(2, self._rainbow_bars[i] * 28)
            x = rb_x0 + i * bar_w
            a_bar = 200 if self.speaking else 130
            p.setBrush(QBrush(qcol(bar_col, a_bar)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, rb_cy - h, bar_w - 1, h), 1, 1)

        # ── waveform bars ──
        wy = rb_cy + 18
        N_wave, bw = 40, 7
        wx0 = (W - N_wave * bw) / 2
        for i in range(N_wave):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C, 120)
            elif self.speaking:
                hgt = random.randint(3, 22)
                cl  = qcol(C.PRI) if hgt > 14 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2.5 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B, 160)
            p.fillRect(QRectF(wx0 + i * bw, wy + 22 - hgt, bw - 1.5, hgt), cl)

        # ── microphone indicator ──
        mic_y = wy + 50
        # mic icon circle
        mic_col = C.MUTED_C if self.muted else C.PRI
        mic_r = 16
        # glow
        for g in range(5):
            p.setPen(QPen(qcol(mic_col, 15 - g * 2), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, mic_y), mic_r + g + 2, mic_r + g + 2)
        p.setBrush(QBrush(qcol(mic_col, 30)))
        p.setPen(QPen(qcol(mic_col, 200), 1.5))
        p.drawEllipse(QPointF(cx, mic_y), mic_r, mic_r)
        # mic shape inside
        p.setPen(QPen(qcol(mic_col), 2))
        p.drawRoundedRect(QRectF(cx - 4, mic_y - 9, 8, 13), 3, 3)
        p.drawArc(QRectF(cx - 7, mic_y - 6, 14, 16), 0, -180 * 16)
        p.drawLine(QPointF(cx, mic_y + 10), QPointF(cx, mic_y + 13))

        # status text
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            txt, col = "LISTENING TO YOU",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.drawText(QRectF(0, mic_y + 18, W, 18), Qt.AlignmentFlag.AlignCenter, txt)

# ────────────────── holographic globe widget ──────────────────

class HolographicGlobeWidget(QWidget):
    """Mini Global Overview — animated holographic globe with radar sweep, location dots, and neon glow."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self._angle = 0.0
        self._tick = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(33)  # ~30 FPS: lower CPU contention with automation
        # Location dots (lat, lon, color, label)
        self._locations = [
            (40.7, -74.0,  C.PRI,    "NYC"),
            (51.5, -0.1,   C.GREEN,  "LON"),
            (35.7, 139.7,  C.ACC,    "TKY"),
            (-33.9, 151.2, C.RED,    "SYD"),
            (28.7, 77.1,   C.ACC2,   "DEL"),
            (-23.6, -46.6, C.GREEN_D,"SAO"),
            (55.8, 37.6,   C.MUTED_C,"MOS"),
            (39.9, 116.4,  C.GREEN,  "BJS"),
            (48.9, 2.3,    C.PRI,    "PAR"),
            (-26.2, 28.0,  C.ACC,    "JNB"),
        ]
        # Moving dots for radar blips
        self._blips: list[list[float]] = []
        self._connection_lines: list[tuple[float, float, float, float]] = []
        self._build_connections()

    def _build_connections(self):
        """Create lines connecting random pairs of locations."""
        import random as rnd
        idxs = list(range(len(self._locations)))
        for _ in range(8):
            i, j = rnd.sample(idxs, 2)
            li, lj = self._locations[i], self._locations[j]
            self._connection_lines.append((
                math.radians(li[0]), math.radians(li[1]),
                math.radians(lj[0]), math.radians(lj[1]),
            ))

    def _step(self):
        self._tick += 1
        self._angle = (self._angle + 0.6) % 360
        # Manage radar blips
        if self._tick % 12 == 0 and len(self._blips) < 6:
            import random as rnd
            loc = rnd.choice(self._locations)
            self._blips.append([math.radians(loc[0]), math.radians(loc[1]), 0.0, 1.0])
        self._blips = [
            [b[0], b[1], b[2] + 0.015, b[3] - 0.015]
            for b in self._blips if b[3] > 0
        ]
        self.update()

    def _project(self, lat_rad: float, lon_rad: float, R: float, cx: float, cy: float, rot: float):
        """Project 3D lat/lon to 2D screen coords with rotation."""
        lon = lon_rad + rot
        x3d = math.cos(lat_rad) * math.sin(lon)
        y3d = math.sin(lat_rad)
        z3d = math.cos(lat_rad) * math.cos(lon)
        sx = cx + R * x3d
        sy = cy - R * y3d
        return sx, sy, z3d

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        R = min(W, H) / 2 - 20
        rot = math.radians(self._angle)

        # ── Background ──
        p.fillRect(self.rect(), qcol("#000a12", 0))

        # ── Outer glow circle ──
        for i in range(12, 0, -1):
            r_glow = R + 8 + i * 3
            alpha = 8 - i * 0.6
            p.setPen(QPen(qcol(C.PRI, max(1, int(alpha))), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r_glow, r_glow)

        # ── Globe body ──
        p.setPen(QPen(qcol(C.PRI, 100), 1.5))
        p.setBrush(QBrush(qcol("#001520", 90)))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # ── Latitude lines ──
        p.setPen(QPen(qcol(C.PRI, 50), 0.8))
        for lat_deg in range(-60, 90, 30):
            lat = math.radians(lat_deg)
            y = cy - R * math.sin(lat)
            rx = abs(R * math.cos(lat))
            if rx > 2:
                p.drawEllipse(QRectF(cx - rx, y - 1, rx * 2, 2))

        # ── Longitude lines (rotating) ──
        for lon_deg in range(0, 180, 20):
            lon = math.radians(lon_deg) + rot
            pts_front = []
            pts_back = []
            for lat_deg in range(-90, 91, 4):
                lat = math.radians(lat_deg)
                x3d = math.cos(lat) * math.sin(lon)
                y3d = math.sin(lat)
                z3d = math.cos(lat) * math.cos(lon)
                sx = cx + R * x3d
                sy = cy - R * y3d
                if z3d >= 0:
                    pts_front.append((sx, sy))
                else:
                    pts_back.append((sx, sy))
            # Draw back half dimmer
            p.setPen(QPen(qcol(C.PRI, 20), 0.5))
            if len(pts_back) > 1:
                for k in range(len(pts_back) - 1):
                    p.drawLine(QPointF(pts_back[k][0], pts_back[k][1]),
                               QPointF(pts_back[k+1][0], pts_back[k+1][1]))
            # Draw front half brighter
            p.setPen(QPen(qcol(C.PRI, 60), 0.8))
            if len(pts_front) > 1:
                for k in range(len(pts_front) - 1):
                    p.drawLine(QPointF(pts_front[k][0], pts_front[k][1]),
                               QPointF(pts_front[k+1][0], pts_front[k+1][1]))

        # ── Outer ring with tick marks ──
        ring_r = R + 4
        # Ring glow
        p.setPen(QPen(qcol(C.PRI, 60), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)
        # Tick marks
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            is_major = deg % 30 == 0
            inner = ring_r - 4 if is_major else ring_r - 2
            outer = ring_r + 4 if is_major else ring_r + 2
            p.setPen(QPen(qcol(C.PRI, 80 if is_major else 40), 1.2 if is_major else 0.7))
            p.drawLine(
                QPointF(cx + inner * math.cos(rad), cy - inner * math.sin(rad)),
                QPointF(cx + outer * math.cos(rad), cy - outer * math.sin(rad)),
            )

        # ── Connection lines between locations ──
        p.setPen(QPen(qcol(C.PRI, 30), 0.5))
        for lat1, lon1, lat2, lon2 in self._connection_lines:
            sx1, sy1, z1 = self._project(lat1, lon1, R, cx, cy, rot)
            sx2, sy2, z2 = self._project(lat2, lon2, R, cx, cy, rot)
            if z1 > 0.05 and z2 > 0.05:
                p.drawLine(QPointF(sx1, sy1), QPointF(sx2, sy2))

        # ── Location dots ──
        for lat_d, lon_d, col, _ in self._locations:
            sx, sy, z = self._project(math.radians(lat_d), math.radians(lon_d), R, cx, cy, rot)
            if z > 0.05:
                # Pulses
                pulse_r = 4 + 2 * math.sin(self._tick * 0.08 + lat_d)
                for g in range(4):
                    a = max(1, int(40 - g * 9))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(qcol(col, a)))
                    p.drawEllipse(QPointF(sx, sy), pulse_r + g * 3, pulse_r + g * 3)
                p.setBrush(QBrush(qcol(col)))
                p.setPen(QPen(qcol(C.PRI, 120), 0.7))
                p.drawEllipse(QPointF(sx, sy), 3.5, 3.5)

        # ── Moving radar blips ──
        for blip in self._blips:
            lat, lon, rad_sz, opacity = blip
            sx, sy, z = self._project(lat, lon + rot * 0.3, R, cx, cy, rot)
            if z > 0.05:
                sz = 2 + rad_sz * 12
                a = max(1, int(opacity * 180))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcol(C.PRI, a)))
                p.drawEllipse(QPointF(sx, sy), sz, sz)
                # Blip ring
                p.setPen(QPen(qcol(C.PRI, a // 2), 0.7))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(sx, sy), sz * 2, sz * 2)

        # ── Radar sweep effect ──
        sweep_angle = (self._tick * 3) % 360
        p.setPen(QPen(qcol(C.PRI, 50), 1.5))
        p.setBrush(QBrush(qcol(C.PRI, 15)))
        p.drawPie(
            QRectF(cx - R, cy - R, R * 2, R * 2),
            int((-sweep_angle - 15) * 16),
            int(30 * 16),
        )

        # ── Spinning orbital ring ──
        orbit_angle = (self._tick * 2) % 360
        orbit_r = R + 12
        # Dashed orbiting dots
        for i in range(4):
            a = math.radians(orbit_angle + i * 90)
            ox = cx + orbit_r * math.cos(a)
            oy = cy - orbit_r * math.sin(a)
            alpha = 100 + 80 * math.sin(self._tick * 0.05 + i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ACC2, int(alpha))))
            p.drawEllipse(QPointF(ox, oy), 3, 3)

        # ── HUD header text ──
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI, 180), 1))
        p.drawText(QRectF(8, 4, W - 16, 18), Qt.AlignmentFlag.AlignLeft,
                   "► SATELLITE TRACKING")
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM, 140), 1))
        p.drawText(QRectF(8, 18, W - 16, 14), Qt.AlignmentFlag.AlignLeft,
                   f"  GLOBAL OVERVIEW  •  {len(self._locations)} ACTIVE NODES")

        # ── Corner brackets ──
        bl = 12
        bc = qcol(C.PRI, 130)
        p.setPen(QPen(bc, 1.5))
        margin = 4
        # Top-left
        p.drawLine(QPointF(margin, margin), QPointF(margin + bl, margin))
        p.drawLine(QPointF(margin, margin), QPointF(margin, margin + bl))
        # Top-right
        p.drawLine(QPointF(W - margin - bl, margin), QPointF(W - margin, margin))
        p.drawLine(QPointF(W - margin, margin), QPointF(W - margin, margin + bl))
        # Bottom-left
        p.drawLine(QPointF(margin, H - margin), QPointF(margin + bl, H - margin))
        p.drawLine(QPointF(margin, H - margin), QPointF(margin, H - margin - bl))
        # Bottom-right
        p.drawLine(QPointF(W - margin - bl, H - margin), QPointF(W - margin, H - margin))
        p.drawLine(QPointF(W - margin, H - margin), QPointF(W - margin, H - margin - bl))

# ────────────────── log widget ──────────────────

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 8))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(1, 10, 18, 180);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        elif tl.startswith("system"):  self._tag = "sys"
        elif tl.startswith("user:"):   self._tag = "you"
        else:                          self._tag = "sys"
        self._tmr.start(5)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

# ────────────────── file helpers (unchanged) ──────────────────

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"

# ────────────────── file drop zone (unchanged) ──────────────────

class GifSideWidget(QWidget):
    """Downloads an animated GIF once and plays it using QMovie."""

    def __init__(
        self,
        gif_url: str,
        cache_path: str,
        parent: QWidget | None = None,
        width: int = 150,
        height: int = 120,
    ):
        super().__init__(parent)
        self._gif_url = gif_url
        self._cache_path = Path(cache_path)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.setStyleSheet("background: transparent; border: none;")

        self._label = QLabel("Loading…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._label.setStyleSheet(f"color: {C.TEXT_DIM};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._label)

        # Placeholder player widget
        self._movie_label = QLabel()
        self._movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._movie_label)

        self._movie: QMovie | None = None
        self._try_start_from_cache()

    def _try_start_from_cache(self):
        if self._cache_path.exists() and self._cache_path.stat().st_size > 100:
            self._start_movie(self._cache_path)
            return

        # download in background thread
        threading.Thread(target=self._download_and_start, daemon=True).start()

    def _download_and_start(self):
        try:
            urllib.request.urlretrieve(self._gif_url, self._cache_path.as_posix())
            # Start movie on UI thread
            QTimer.singleShot(0, lambda: self._start_movie(self._cache_path))
        except Exception:
            QTimer.singleShot(0, lambda: self._label.setText("GIF load failed"))

    def _start_movie(self, path: Path):
        movie = QMovie(str(path))
        if movie.isValid():
            self._movie = movie
            self._movie_label.setMovie(movie)
            self._label.hide()
            movie.start()
        else:
            self._label.setText("GIF invalid")


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(70)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 4
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 5, 5)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.2, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 5, 5)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy + 2))
        p.drawLine(QPointF(cx - 6, cy - 4), QPointF(cx, cy - 10))
        p.drawLine(QPointF(cx + 6, cy - 4), QPointF(cx, cy - 10))
        p.drawLine(QPointF(cx - 10, cy + 2), QPointF(cx + 10, cy + 2))
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 6, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Drop file · Click to Browse")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 16))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 16, W, 24), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.drawText(QRectF(0, cy + 8, W, 14), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        try:
            size_str = _fmt_size(path.stat().st_size)
        except Exception:
            size_str = "?"
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 8, 40
        icon_font = QFont("Segoe UI Emoji", 16) if _OS == "Windows" else QFont("Arial", 16)
        p.setFont(icon_font)
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 4
        tw = W - tx - 28

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 30 else path.name[:27] + "..."
        p.drawText(QRectF(tx, H * 0.2, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.2 + 16, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 26, 0, 22, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 26:
            z.clear_file()
        else:
            z.mousePressEvent(e)

# ────────────────── setup overlay (unchanged) ──────────────────

class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)

# ════════════════════════════════════════════════════════════════
#  MAIN WINDOW — rewritten layout to match photo
# ════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _task_sig  = pyqtSignal(str, str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S — VEER INDUS")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_cancel_task   = None
        self._muted           = False
        self._current_file: str | None = None
        self._command_history: list[str] = []
        self._history_index = -1

        # ── background central widget ──
        central = _BgWidget()
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(0)

        # ── floating panel ──
        self._panel = _FloatingPanel()
        panel_lay = QVBoxLayout(self._panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(0)

        # header
        panel_lay.addWidget(self._build_header())

        # body: left | center | right
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # vertical separator
        sep_l = QFrame()
        sep_l.setFrameShape(QFrame.Shape.VLine)
        sep_l.setFixedWidth(1)
        sep_l.setStyleSheet(f"color: {C.BORDER};")
        body.addWidget(sep_l)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hud.show_centerpiece = False # Hide default orb to show GIF instead

        # Center the Iron Man GIF on the HUD
        self._gif_main = GifSideWidget(
            gif_url="https://files.manuscdn.com/user_upload_by_module/session_file/310519663755367509/teCJiUcwuGIYztaP.gif",
            cache_path=str(BASE_DIR / "gif_cache" / "iron_man_newlink.gif"),
            parent=self.hud
        )
        hud_lay = QVBoxLayout(self.hud)
        hud_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Place GIF exactly in the same vertical area as MINI GLOBAL OVERVIEW
        hud_lay.setContentsMargins(0, 0, 0, 250)
        hud_lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        hud_lay.addWidget(self._gif_main)
        body.addWidget(self.hud, stretch=5)


        sep_r = QFrame()
        sep_r.setFrameShape(QFrame.Shape.VLine)
        sep_r.setFixedWidth(1)
        sep_r.setStyleSheet(f"color: {C.BORDER};")
        body.addWidget(sep_r)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        panel_lay.addLayout(body, stretch=1)
        panel_lay.addWidget(self._build_footer())

        outer.addWidget(self._panel)

        # ── timers ──
        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._task_sig.connect(self._apply_task)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()
        else:
            # Boot log — matching photo
            boot_lines = [
                "SYSTEM [10:32]: User 'Sid' initialized",
                "USER: Hey Jarvis, get me initialized for Delhi.",
                "JARVIS: Weather Delhi: 29°C, Clear.",
                "SYSTEM [10:32]: Optimized energy grid.",
                "USER: Analyze Project Chimera.",
                "JARVIS: Analyzing project data.",
                "SYSTEM [10:33]: Optimized energy grid.",
                "JARVIS: Optimized energy.",
                "USER: Analyze Project Chimera.",
                "JARVIS: Analyzing project data streams.",
                "JARVIS: Analyzing project data for these.",
                "USER: Command for and log history received.",
                "JARVIS: Commanding panel restructuring",
            ]
            for line in boot_lines:
                self._log.append_log(line)

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_focus = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_focus.activated.connect(self._focus_command_input)
        sc_clear = QShortcut(QKeySequence("Escape"), self)
        sc_clear.activated.connect(self._clear_command_input)

    # ── header ──

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(48)
        w.setStyleSheet(f"background: rgba(4, 14, 24, 200); border-bottom: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge("VEER INDUS", C.PRI_DIM))
        lay.addSpacing(10)
        lay.addWidget(_badge("● ONLINE", C.GREEN))
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(0)
        title = QLabel("JA AI ASSISTANT")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Courier New", 15, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 4px;")
        mid.addWidget(title)
        sub = QLabel("Just A Rather Very Intelligent System")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Courier New", 6))
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(sub)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(1)
        self._state_badge = QLabel("●  STANDBY")
        self._state_badge.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._state_badge.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._state_badge.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        right_col.addWidget(self._state_badge)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 6))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    # ── left panel ──

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: rgba(0, 8, 16, 180);")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        # SYSTEM STATUS header
        hdr = QLabel("◈ SYSTEM STATUS")
        hdr.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.WHITE}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        sub_hdr = QLabel("Real-Time Vitals")
        sub_hdr.setFont(QFont("Courier New", 7))
        sub_hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(sub_hdr)
        lay.addSpacing(2)

        # mini frequency bar decoration
        self._freq_widget = _FreqBarDecoration()
        lay.addWidget(self._freq_widget)
        lay.addSpacing(3)

        self._bar_cpu = MetricBar("CPU", C.PRI,  "💻")
        self._bar_mem = MetricBar("MEM", C.ACC2, "📊")
        self._bar_net = MetricBar("NET", C.GREEN, "📡")
        self._bar_gpu = MetricBar("GPU", C.ACC,  "⚡")
        self._bar_tmp = MetricBar("TMP", "#ff6688", "🌡")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        # Environment
        self._env_widget = EnvironmentWidget()
        lay.addWidget(self._env_widget)

        lay.addSpacing(4)

        # Radar chart
        self._radar = RadarChartWidget()
        lay.addWidget(self._radar, stretch=1)

        return w

    # ── right panel ──

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: rgba(0, 8, 16, 180);")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.WHITE}; background: transparent; "
                            f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 3px;")
            return l

        lay.addWidget(_sec("CMD LOG / HISTORY"))

        self._log = LogWidget()

        lay.addWidget(self._log, stretch=3)

        # command input row

        input_row = QHBoxLayout()
        input_row.setSpacing(4)
        command_prefix = QLabel("⌁")
        command_prefix.setFixedWidth(18)
        command_prefix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        command_prefix.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        command_prefix.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        command_prefix.setToolTip("Natural-language command channel")
        input_row.addWidget(command_prefix)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type command…")
        self._input.setFont(QFont("Courier New", 8))
        self._input.setFixedHeight(26)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(0, 13, 20, 200); color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 6px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, stretch=1)

        send = QPushButton("▸")
        send.setFixedSize(26, 26)
        send.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        send.setToolTip("Send command (Enter)")
        input_row.addWidget(send)
        lay.addLayout(input_row)

        # file drop zone
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        # mute button
        self._mute_btn = QPushButton("🎙  MIC ACTIVE")
        self._mute_btn.setFixedHeight(26)
        self._mute_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        # High-frequency commands: keep them visible and one click away.
        quick_hdr = QLabel("QUICK CONTROL")
        quick_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        quick_hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; margin-top: 2px;")
        lay.addWidget(quick_hdr)
        quick_row = QHBoxLayout(); quick_row.setSpacing(4)
        for label, command in (
            ("STATUS", "Show current system status"),
            ("SCREEN", "Analyze my current screen"),
            ("HELP", "Show everything you can do"),
            ("CANCEL", None),
        ):
            btn = QPushButton(label)
            btn.setFixedHeight(23)
            btn.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(command)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {C.PANEL}; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 3px; padding: 1px 3px; }}
                QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI_DIM};
                    background: {C.PRI_GHO}; }}
                QPushButton:pressed {{ background: {C.PANEL3}; }}
            """)
            if command is None:
                btn.setStyleSheet(btn.styleSheet().replace(C.TEXT_MED, C.RED))
                btn.clicked.connect(self._cancel_task)
            else:
                btn.clicked.connect(lambda checked=False, cmd=command: self._quick_command(cmd))
            quick_row.addWidget(btn)
        lay.addLayout(quick_row)

        self._task_lbl = QLabel("READY  //  Awaiting command")
        self._task_lbl.setMinimumHeight(30)
        self._task_lbl.setWordWrap(True)
        self._task_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._task_lbl.setStyleSheet(
            f"color: {C.GREEN}; background: rgba(0, 20, 18, 150); "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px;"
        )
        lay.addWidget(self._task_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("MINI GLOBAL OVERVIEW"))
        self._globe = HolographicGlobeWidget()
        lay.addWidget(self._globe, stretch=2)

        return w

    # ── footer ──

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: rgba(4, 14, 24, 200); border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 6))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen  ·  [Ctrl+L] Command"))
        lay.addStretch()
        lay.addWidget(_fl("VEER INDUS  ·  JARVIS CORE  ·  CLASSIFIED"))
        lay.addStretch()
        lay.addWidget(_fl("© JARVIS", C.PRI_DIM))
        return w

    # ── metric updates ──

    def _update_metrics(self):
        snap = _metrics.snapshot()

        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        mem = snap["mem"]
        vm = psutil.virtual_memory()
        used_gb = vm.used / (1024**3)
        total_gb = vm.total / (1024**3)
        self._bar_mem.set_value(mem, f"{used_gb:.1f}G/{total_gb:.0f}GB")

        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)
        self._bar_net.set_value(net_pct, net_str)

        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        # update radar chart
        self._radar.set_values([
            cpu / 100,
            mem / 100,
            min(1, net / 10),
            (gpu / 100) if gpu >= 0 else 0.3,
        ])

    # ── interactions ──

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            cw = self.centralWidget()
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        try:
            size = _fmt_size(p.stat().st_size)
        except Exception:
            size = "?"
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MIC MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MIC ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        if not self._command_history or self._command_history[-1] != txt:
            self._command_history.append(txt)
        self._history_index = -1
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _quick_command(self, command: str):
        self._input.setText(command)
        self._send()

    def _focus_command_input(self):
        self._input.setFocus()
        self._input.selectAll()

    def _clear_command_input(self):
        self._input.clear()
        self._input.setFocus()

    def _cancel_task(self):
        if self.on_cancel_task:
            self.on_cancel_task()
        else:
            self._log.append_log("SYS: No active task to cancel.")

    def eventFilter(self, obj, event):
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up and self._command_history:
                if self._history_index < 0:
                    self._history_index = len(self._command_history)
                self._history_index = max(0, self._history_index - 1)
                self._input.setText(self._command_history[self._history_index])
                return True
            if key == Qt.Key.Key_Down and self._command_history:
                self._history_index = min(len(self._command_history), self._history_index + 1)
                self._input.setText("" if self._history_index == len(self._command_history)
                                    else self._command_history[self._history_index])
                return True
        return super().eventFilter(obj, event)

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        normalized = str(state or "STANDBY").upper()
        palette = {
            "LISTENING": C.GREEN,
            "SPEAKING": C.PRI,
            "THINKING": C.ACC2,
            "WORKING": C.ACC,
            "ERROR": C.RED,
            "MUTED": C.MUTED_C,
            "STANDBY": C.ACC2,
        }
        color = palette.get(normalized, C.TEXT_MED)
        if hasattr(self, "_state_badge"):
            self._state_badge.setText(f"●  {normalized}")
            self._state_badge.setStyleSheet(f"color: {color}; background: transparent;")

    def _apply_task(self, phase: str, detail: str):
        colors = {"READY": C.GREEN, "RECEIVED": C.PRI, "EXECUTING": C.ACC,
                  "VERIFYING": C.ACC2, "RECOVERY": C.RED}
        phase = str(phase or "READY").upper()
        color = colors.get(phase, C.TEXT_MED)
        self._task_lbl.setText(f"{phase}  //  {detail}")
        self._task_lbl.setStyleSheet(
            f"color: {color}; background: rgba(0, 20, 18, 150); "
            f"border: 1px solid {color}; border-radius: 3px; padding: 5px;"
        )

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")


# ────────────────── mini freq bar decoration ──────────────────

class _FreqBarDecoration(QWidget):
    """Tiny colorful frequency bars at top of system status (decorative)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._tick = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(100)

    def _step(self):
        self._tick += 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        n = 20
        bw = max(2, (W - 8) / n)
        colors = ["#ff6b00", "#ff3355", "#ffcc00", "#00ff88", "#00d4ff",
                  "#4488ff", "#cc44ff", "#ff6b00", "#00ff88", "#00d4ff"]
        for i in range(n):
            h = 4 + int(10 * abs(math.sin(self._tick * 0.15 + i * 0.7)))
            ci = i % len(colors)
            p.setBrush(QBrush(qcol(colors[ci], 180)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(4 + i * bw, H - h - 2, bw - 1, h), 1, 1)


# ════════════════════════════════════════════════════════════════
#  PUBLIC API (unchanged)
# ════════════════════════════════════════════════════════════════

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def update_task(self, phase: str, detail: str):
        self._win._task_sig.emit(str(phase), str(detail))

    @property
    def on_cancel_task(self):
        return self._win.on_cancel_task

    @on_cancel_task.setter
    def on_cancel_task(self, cb):
        self._win.on_cancel_task = cb

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
