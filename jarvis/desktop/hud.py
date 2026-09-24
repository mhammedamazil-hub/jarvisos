"""JARVIS desktop HUD — runs as the desktop layer (Openbox rule keeps it *below*
normal windows, so it never blocks applications). Hotkeys reach it through a local
socket (`jarvis-hudctl`)."""
from __future__ import annotations

import math
import os
import random
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal, QSize
from PyQt5.QtGui import QColor, QPainter, QPen, QRadialGradient, QBrush, QFont, QPainterPath, QConicalGradient
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QListWidget,
                             QListWidgetItem, QDialog, QPlainTextEdit, QProgressBar, QSizePolicy, QFrame,
                             QGraphicsOpacityEffect)

from . import theme
from .theme import Panel, button, CYAN, BLUE, VIOLET, WHITE, DIM, WARN, DANGER, OK, MODE_COLORS
from .core_link import CoreLink


def hud_socket() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"jarvis-hud-{os.getuid()}")


# ============================================================== visual widgets
class Reactor(QWidget):
    """Central JARVIS core: arc-reactor, orbital rings, particles. Detail level follows settings."""

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.t = 0.0
        self.state = "idle"        # idle / thinking / working / listening / alert / stopped
        self.particles = [(random.random() * 6.283, random.uniform(0.55, 1.0), random.uniform(0.2, 1)) for _ in range(60)]
        self.setMinimumSize(320, 320)
        self.setAccessibleName("JARVIS core status")

    def advance(self, dt: float):
        self.t += dt * self.cfg.get("ui.animation_speed", 1.0)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        c = QPointF(w / 2, h / 2)
        R = min(w, h) / 2 - 8
        cin = self.cfg.get("ui.mode") == "cinematic"
        bright = self.cfg.get("ui.brightness", 1.0)
        reduced = self.cfg.get("ui.reduced_motion", False)
        t = 0 if reduced else self.t
        col = {"idle": QColor(CYAN), "thinking": QColor(VIOLET), "working": QColor(CYAN), "listening": QColor(OK),
               "alert": QColor(WARN), "stopped": QColor(DANGER)}[self.state]

        def c_(a):
            q = QColor(col)
            q.setAlpha(max(0, min(255, int(a * bright))))
            return q
        # glow
        g = QRadialGradient(c, R)
        pulse = 0.5 + 0.5 * math.sin(t * (3 if self.state in ("thinking", "working") else 1.2))
        g.setColorAt(0, c_(120 + 60 * pulse))
        g.setColorAt(0.35, c_(40))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, R, R)
        # outer tick ring (always)
        p.setPen(QPen(c_(90), 1))
        for i in range(120):
            a = i / 120 * 2 * math.pi
            r1 = R * (0.93 if i % 10 else 0.88)
            p.drawLine(QPointF(c.x() + math.cos(a) * r1, c.y() + math.sin(a) * r1),
                       QPointF(c.x() + math.cos(a) * R * 0.97, c.y() + math.sin(a) * R * 0.97))
        # orbital rings
        if self.cfg.get("ui.orbital_rings", True):
            for k, (rr, speed, span, width) in enumerate(((0.82, 0.6, 250, 2), (0.72, -0.9, 120, 3),
                                                          (0.64, 1.4, 60, 2)) if cin else ((0.78, 0.4, 270, 2),)):
                p.setPen(QPen(c_(200 - k * 40), width, cap=Qt.FlatCap))
                rect = QRectF(c.x() - R * rr, c.y() - R * rr, 2 * R * rr, 2 * R * rr)
                start = int((t * speed * 57.3) % 360 * 16)
                p.drawArc(rect, start, span * 16)
                p.drawArc(rect, start + 180 * 16, int(span * 0.3) * 16)
        if cin:
            # tilted ellipse orbit (holographic depth)
            p.save()
            p.translate(c)
            p.rotate(t * 8 % 360)
            p.setPen(QPen(QColor(155, 107, 255, int(90 * bright)), 1))
            p.drawEllipse(QPointF(0, 0), R * 0.9, R * 0.35)
            p.restore()
        # particles
        if self.cfg.get("ui.particles") and cin and not reduced:
            for i, (a0, rr, sp) in enumerate(self.particles):
                a = a0 + t * sp * 0.4
                pr = R * rr * (0.95 + 0.05 * math.sin(t * 2 + i))
                p.setPen(Qt.NoPen)
                p.setBrush(c_(80 + 100 * sp))
                p.drawEllipse(QPointF(c.x() + math.cos(a) * pr, c.y() + math.sin(a) * pr), 1.6, 1.6)
        # reactor segments
        segs = 10
        p.setPen(Qt.NoPen)
        for i in range(segs):
            a = i / segs * 360 + (t * 20 if self.state in ("thinking", "working") else 0)
            path = QPainterPath()
            r_out, r_in = R * 0.5, R * 0.36
            rect_o = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
            rect_i = QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in)
            path.arcMoveTo(rect_o, a)
            path.arcTo(rect_o, a, 360 / segs - 6)
            path.arcTo(rect_i, a + 360 / segs - 6, -(360 / segs - 6))
            path.closeSubpath()
            p.setBrush(c_(110 + 80 * (0.5 + 0.5 * math.sin(t * 2 + i))))
            p.drawPath(path)
        # inner core
        g2 = QRadialGradient(c, R * 0.3)
        g2.setColorAt(0, QColor(255, 255, 255, int(230 * bright) if bright <= 1 else 255))
        g2.setColorAt(0.4, c_(220))
        g2.setColorAt(1, c_(0))
        p.setBrush(QBrush(g2))
        p.drawEllipse(c, R * 0.3, R * 0.3)
        p.setPen(QPen(c_(255), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, R * 0.22, R * 0.22)
        # label
        p.setPen(QColor(WHITE))
        f = QFont(theme.FONT_UI, max(9, int(R * 0.07)), QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(f)
        p.drawText(QRectF(c.x() - R, c.y() + R * 0.55, 2 * R, R * 0.2), Qt.AlignCenter, "J.A.R.V.I.S")
        f.setPointSize(max(8, int(R * 0.045)))
        p.setFont(f)
        p.setPen(col)
        p.drawText(QRectF(c.x() - R, c.y() + R * 0.72, 2 * R, R * 0.15), Qt.AlignCenter, self.state.upper())


class Waveform(QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg, self.t, self.level = cfg, 0.0, 0.15
        self.setMinimumHeight(56)
        self.setAccessibleName("activity waveform")

    def advance(self, dt):
        self.t += dt * self.cfg.get("ui.animation_speed", 1.0)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = int(self.cfg.get("ui.waveform_detail", 24))
        w, h = self.width(), self.height()
        bw = w / n
        cin = self.cfg.get("ui.mode") == "cinematic"
        for i in range(n):
            x = i / n
            env = math.sin(x * math.pi)
            v = env * (0.15 + self.level * (0.5 + 0.5 * math.sin(self.t * 6 + i * 0.7) * math.sin(self.t * 2.3 + i * 0.31)))
            bh = max(2, abs(v) * h * 0.9)
            col = QColor(CYAN) if not cin else QColor.fromHsvF((0.52 + 0.12 * x) % 1, 0.75, 1.0)
            col.setAlpha(200)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(i * bw + bw * 0.2, h / 2 - bh / 2, bw * 0.6, bh), 2, 2)


class Gauge(QWidget):
    def __init__(self, label: str):
        super().__init__()
        self.label, self.value, self.text = label, 0.0, "--"
        self.setMinimumSize(92, 92)
        self.setAccessibleName(label)

    def set(self, v: float, text: str):
        self.value, self.text = max(0.0, min(1.0, v)), text
        self.setAccessibleDescription(f"{self.label} {text}")
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height()) - 10
        r = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        p.setPen(QPen(QColor(41, 216, 255, 40), 6, cap=Qt.FlatCap))
        p.drawArc(r, 225 * 16, -270 * 16)
        col = QColor(OK if self.value < 0.6 else WARN if self.value < 0.85 else DANGER)
        p.setPen(QPen(col, 6, cap=Qt.RoundCap))
        p.drawArc(r, 225 * 16, int(-270 * 16 * self.value))
        p.setPen(QColor(WHITE))
        p.setFont(QFont(theme.FONT_MONO, 12, QFont.Bold))
        p.drawText(r.adjusted(0, -s * 0.08, 0, -s * 0.08), Qt.AlignCenter, self.text)
        p.setPen(QColor(DIM))
        p.setFont(QFont(theme.FONT_UI, 7, QFont.Bold))
        p.drawText(r.adjusted(0, s * 0.72, 0, 0), Qt.AlignHCenter | Qt.AlignTop, self.label)


# ============================================================== dialogs
class ApprovalDialog(QDialog):
    def __init__(self, req: dict, parent=None):
        super().__init__(parent, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Dialog)
        self.req, self.result_status = req, "denied"
        self.setObjectName("root")
        self.setMinimumWidth(620)
        self.setAccessibleName("JARVIS requests approval")
        a = req["assessment"]
        risk_col = {"NONE": OK, "LOW": OK, "MEDIUM": WARN, "HIGH": DANGER, "CRITICAL": DANGER}[a["risk"]]
        lay = QVBoxLayout(self)
        box = Panel("⚠  JARVIS requests approval")
        lay.addWidget(box)
        grid = QGridLayout()
        rows = [("Action", req["title"]), ("Reason", req["reason"]),
                ("Risk", f"<b style='color:{risk_col}'>{a['risk']}</b>  ·  {a['op']}"),
                ("Reversible", "Yes" if a["reversible"] else "<b style='color:#ff4d6d'>NO</b>"),
                ("Backup", "Available" if req.get("backup_available") else "Not applicable"),
                ("Why", "; ".join(a.get("reasons", [])) or "-")]
        for i, (k, v) in enumerate(rows):
            kl = QLabel(k.upper())
            kl.setObjectName("dim")
            vl = QLabel(v)
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(kl, i, 0, Qt.AlignTop)
            grid.addWidget(vl, i, 1)
        grid.setColumnStretch(1, 1)
        box.lay.addLayout(grid)
        self.details = QPlainTextEdit(req["details"])
        self.details.setReadOnly(True)
        self.details.setFont(QFont(theme.FONT_MONO, 10))
        self.details.setMaximumHeight(160)
        self.details.setVisible(a["risk"] in ("HIGH", "CRITICAL"))
        box.lay.addWidget(self.details)
        row = QHBoxLayout()
        self.btn_ok = button("APPROVE", "ok", "Approve this action (Enter)", lambda: self.done_with("approved"))
        row.addWidget(self.btn_ok)
        if a["risk"] not in ("HIGH", "CRITICAL"):
            row.addWidget(button("APPROVE ALL SIMILAR", "", "Approve similar low/medium-risk steps for this session",
                                 lambda: self.done_with("approve_all")))
        row.addWidget(button("DENY", "", "Deny (Esc)", lambda: self.done_with("denied")))
        row.addWidget(button("EDIT PLAN", "", "Deny and edit the plan", lambda: self.done_with("edit")))
        row.addWidget(button("SHOW DETAILS", "", "Show exact commands",
                             lambda: self.details.setVisible(not self.details.isVisible())))
        if req.get("backup_available"):
            row.addWidget(button("CREATE BACKUP", "", "A backup is always made before file changes; approve to proceed",
                                 lambda: self.done_with("approved")))
        row.addWidget(button("■ STOP JARVIS", "danger", "Emergency stop", lambda: self.done_with("stop")))
        box.lay.addLayout(row)
        self.btn_ok.setDefault(a["risk"] not in ("CRITICAL",))
        if a["risk"] == "CRITICAL":
            self.btn_ok.setText("I UNDERSTAND — APPROVE")

    def done_with(self, status):
        self.result_status = status
        self.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            return self.done_with("denied")
        super().keyPressEvent(e)


class Launcher(QDialog):
    """Super+A: searchable launcher over the live .desktop registry."""

    def __init__(self, core: CoreLink, parent=None):
        super().__init__(parent, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Dialog)
        self.core = core
        self.setObjectName("root")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        box = Panel("Applications")
        lay.addWidget(box)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search applications…")
        self.q.setAccessibleName("application search")
        self.list = QListWidget()
        box.lay.addWidget(self.q)
        box.lay.addWidget(self.list)
        self.apps = core.call("apps") or []
        self.q.textChanged.connect(self.filter)
        self.q.returnPressed.connect(self.launch)
        self.list.itemActivated.connect(lambda _: self.launch())
        self.filter("")

    def filter(self, text):
        self.list.clear()
        t = text.lower()
        for a in self.apps:
            if t in a["name"].lower() or t in a["id"].lower() or t in a.get("categories", "").lower():
                it = QListWidgetItem(f"{a['name']}")
                it.setData(Qt.UserRole, a["id"])
                it.setToolTip(a.get("categories", ""))
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def launch(self):
        it = self.list.currentItem()
        if it:
            self.core.call("launch", app=it.data(Qt.UserRole))
            self.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Up) and self.q.hasFocus():
            self.list.setFocus()
            self.list.keyPressEvent(e)
            return
        super().keyPressEvent(e)


# ============================================================== main HUD
def recent_files(limit: int = 6) -> list[str]:
    f = Path.home() / ".local/share/recently-used.xbel"
    if not f.exists():
        return []
    try:
        items = [(b.get("modified", ""), unquote(urlparse(b.get("href", "")).path))
                 for b in ET.parse(f).getroot().iter("bookmark")]
    except ET.ParseError:
        return []
    return [p for _, p in sorted(items, reverse=True) if p][:limit]


def fmt_bytes(n: float) -> str:
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}T"


class HUD(QWidget):
    def __init__(self, core: CoreLink | None = None):
        super().__init__()
        self.core = core or CoreLink()
        self.setObjectName("root")
        self.setWindowTitle("JARVIS HUD")
        self.setAccessibleName("JARVIS desktop")
        self.cfg: dict = {}
        self.last_event_ts = time.time() - 5
        self.prev_net = None
        self.shown_approvals: set[str] = set()
        self.notifications: list[str] = []
        self.current_task: dict | None = None
        self._build()
        self.refresh_config()
        self.anim = QTimer(self)
        self.anim.timeout.connect(self.tick)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.poll_core)
        self.poll.start(1000)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.apply_anim_rate()
        self._last_tick = time.monotonic()
        self.ipc = QLocalServer(self)
        QLocalServer.removeServer(hud_socket())
        if self.ipc.listen(hud_socket()):
            self.ipc.newConnection.connect(self.on_ipc)
        self.poll_core()
        self.update_clock()

    # ---------------------------------------------------------- layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 14)
        root.setSpacing(12)
        # top bar
        top = QHBoxLayout()
        self.lbl_brand = QLabel("<span style='letter-spacing:6px;font-weight:bold;color:%s'>JARVIS</span>"
                                "<span style='color:%s'>OS</span>" % (CYAN, DIM))
        self.lbl_brand.setStyleSheet("font-size:18px")
        top.addWidget(self.lbl_brand)
        self.lbl_mode = QLabel()
        self.lbl_mode.setAccessibleName("control mode")
        self.lbl_mode.setToolTip("Current control mode — click to change in Settings")
        top.addWidget(self.lbl_mode)
        self.lbl_uimode = QLabel()
        self.lbl_uimode.setObjectName("dim")
        top.addWidget(self.lbl_uimode)
        self.lbl_health = QLabel()
        top.addWidget(self.lbl_health)
        top.addStretch()
        for text, tip, cb in (("NET", "Network settings", lambda: self._spawn(["nm-connection-editor"])),
                              ("BT", "Bluetooth", lambda: self._spawn(["blueman-manager"])),
                              ("VOL", "Audio mixer", lambda: self._spawn(["pavucontrol"])),
                              ("⚙", "JARVIS settings", lambda: self.core.call("launch", app="jarvis-settings")
                               if self.core.online else self._spawn([sys.executable, "-m", "jarvis.desktop.settings"])),
                              ("◐", "Toggle Performance / Cinematic mode", self.toggle_mode),
                              ("—", "Hide HUD (Super+Space to bring back)", self.hide_hud)):
            b = button(text, "ghost", tip, cb)
            top.addWidget(b)
        self.lbl_clock = QLabel()
        self.lbl_clock.setObjectName("big")
        self.lbl_clock.setStyleSheet("font-size:20px")
        top.addWidget(self.lbl_clock)
        self.btn_stop = button("■ STOP JARVIS", "danger", "Emergency stop — halts all automation (Esc)", self.emergency_stop)
        top.addWidget(self.btn_stop)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        # --- system column
        self.col_sys = QVBoxLayout()
        p = Panel("System telemetry")
        g = QGridLayout()
        self.g_cpu, self.g_ram, self.g_disk = Gauge("CPU"), Gauge("RAM"), Gauge("DISK")
        g.addWidget(self.g_cpu, 0, 0)
        g.addWidget(self.g_ram, 0, 1)
        g.addWidget(self.g_disk, 0, 2)
        p.lay.addLayout(g)
        self.lbl_sys = QLabel()
        self.lbl_sys.setObjectName("dim")
        self.lbl_sys.setWordWrap(True)
        self.lbl_sys.setStyleSheet(f"font-family:'{theme.FONT_MONO}';font-size:11px;color:{WHITE}")
        p.lay.addWidget(self.lbl_sys)
        self.col_sys.addWidget(p)
        p2 = Panel("Active")
        self.lbl_active = QLabel("—")
        self.lbl_active.setWordWrap(True)
        p2.lay.addWidget(self.lbl_active)
        self.col_sys.addWidget(p2)
        p3 = Panel("Sensors & AI")
        self.lbl_sensors = QLabel()
        self.lbl_sensors.setWordWrap(True)
        self.lbl_sensors.setTextFormat(Qt.RichText)
        p3.lay.addWidget(self.lbl_sensors)
        self.col_sys.addWidget(p3)
        self.col_sys.addStretch()
        self.sys_wrap = QWidget()
        self.sys_wrap.setLayout(self.col_sys)
        self.sys_wrap.setFixedWidth(330)

        # --- center
        center = QVBoxLayout()
        self.reactor = Reactor(self.cfg)
        self.reactor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center.addWidget(self.reactor, 5)
        self.wave = Waveform(self.cfg)
        center.addWidget(self.wave)
        cmd = Panel("Command")
        row = QHBoxLayout()
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("Tell JARVIS what to do…   e.g. “open blender”, “switch to cinematic mode”, “when I clap twice, open VS Code”")
        self.cmd.setAccessibleName("JARVIS command input")
        self.cmd.setMinimumHeight(40)
        self.cmd.setStyleSheet("font-size:15px")
        self.cmd.returnPressed.connect(self.send_command)
        row.addWidget(self.cmd)
        self.btn_mic = button("MIC", "", "Voice command (Super+V)", self.voice)
        row.addWidget(self.btn_mic)
        row.addWidget(button("SEND", "", "Run command", self.send_command))
        cmd.lay.addLayout(row)
        center.addWidget(cmd)
        tp = Panel("Current task")
        self.lbl_task = QLabel("Standing by.")
        self.lbl_task.setWordWrap(True)
        self.lbl_task.setTextFormat(Qt.RichText)
        self.task_bar = QProgressBar()
        self.task_bar.setTextVisible(False)
        self.task_bar.setMaximumHeight(8)
        trow = QHBoxLayout()
        trow.addWidget(button("PAUSE", "ghost", "Pause task", lambda: self.core.call("pause")))
        trow.addWidget(button("RESUME", "ghost", "Resume task", lambda: self.core.call("resume")))
        trow.addStretch()
        tp.lay.addWidget(self.lbl_task)
        tp.lay.addWidget(self.task_bar)
        tp.lay.addLayout(trow)
        center.addWidget(tp)

        # --- right column
        right = QVBoxLayout()
        lp = Panel("Quick launch")
        grid = QGridLayout()
        for i, (name, app) in enumerate((("Terminal", "x-terminal-emulator"), ("Files", "jarvis-files"),
                                         ("Browser", "firefox-esr"), ("Editor", "jarvis-edit"),
                                         ("VS Code", "code"), ("Blender", "blender"))):
            b = button(name, "", f"Open {name}", lambda _=False, a=app: self.launch(a))
            grid.addWidget(b, i // 2, i % 2)
        lp.lay.addLayout(grid)
        lp.lay.addWidget(button("ALL APPLICATIONS  ⌘A", "", "Application launcher (Super+A)", self.open_launcher))
        right.addWidget(lp)
        self.tasks_panel = Panel("Tasks")
        self.lbl_tasks = QLabel("—")
        self.lbl_tasks.setWordWrap(True)
        self.lbl_tasks.setTextFormat(Qt.RichText)
        self.tasks_panel.lay.addWidget(self.lbl_tasks)
        right.addWidget(self.tasks_panel)
        np_ = Panel("Notifications")
        self.lbl_notes = QLabel("—")
        self.lbl_notes.setWordWrap(True)
        np_.lay.addWidget(self.lbl_notes)
        right.addWidget(np_)
        rp = Panel("Recent files")
        self.lbl_recent = QLabel("—")
        self.lbl_recent.setObjectName("dim")
        self.lbl_recent.setWordWrap(True)
        rp.lay.addWidget(self.lbl_recent)
        right.addWidget(rp)
        right.addStretch()
        self.right_wrap = QWidget()
        self.right_wrap.setLayout(right)
        self.right_wrap.setFixedWidth(330)

        self.body = body
        body.addWidget(self.sys_wrap)
        body.addLayout(center, 1)
        body.addWidget(self.right_wrap)
        root.addLayout(body, 1)

    # ---------------------------------------------------------- behaviour
    def _spawn(self, argv):
        import subprocess
        import shutil
        if shutil.which(argv[0]) or os.path.isabs(argv[0]):
            subprocess.Popen(argv, start_new_session=True)
        else:
            self.notify(f"{argv[0]} is not installed")

    def launch(self, app):
        r = self.core.call("launch", app=app) or {}
        if not r.get("ok"):
            self.notify(r.get("error", "launch failed") + (f" — {r['install_hint']}" if r.get("install_hint") else ""))

    def notify(self, text: str):
        self.notifications = ([time.strftime("%H:%M ") + text] + self.notifications)[:6]
        self.lbl_notes.setText("\n".join(self.notifications))

    def refresh_config(self):
        d = self.core.call("config.get") or {}
        self.cfg.clear()
        self.cfg.update(d.get("settings", {}))
        self.apply_layout()

    def apply_layout(self):
        pos = self.cfg.get("ui.monitor_position", "right")
        self.body.removeWidget(self.sys_wrap)
        self.body.removeWidget(self.right_wrap)
        self.sys_wrap.setVisible(pos != "hidden")
        if pos == "left":
            self.body.insertWidget(0, self.sys_wrap)
            self.body.addWidget(self.right_wrap)
        else:
            self.body.insertWidget(0, self.right_wrap)
            self.body.addWidget(self.sys_wrap)
        mode = self.cfg.get("control.mode", "safe")
        col = MODE_COLORS.get(mode, CYAN)
        self.lbl_mode.setText(f"<span style='border:1px solid {col};color:{col};padding:2px;font-weight:bold'>"
                              f"&nbsp;{mode.upper()} MODE&nbsp;</span>")
        self.lbl_uimode.setText(("◆ CINEMATIC" if self.cfg.get("ui.mode") == "cinematic" else "◇ PERFORMANCE"))
        op = 1.0 - float(self.cfg.get("ui.transparency", 0)) * 0.5
        self.setWindowOpacity(op if self.cfg.get("ui.mode") == "cinematic" else 1.0)
        self.apply_anim_rate()
        self.update()

    def apply_anim_rate(self):
        if not hasattr(self, "anim"):
            return
        fps = int(self.cfg.get("ui.fps", 15))
        if self.cfg.get("ui.reduced_motion"):
            fps = 2
        self.anim.start(int(1000 / max(1, fps)))

    def tick(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.reactor.advance(dt)
        self.wave.advance(dt)

    def update_clock(self):
        self.lbl_clock.setText(time.strftime("%H:%M") + f"<span style='font-size:11px;color:{DIM}'> "
                               + time.strftime("%a %d %b").upper() + "</span>")

    def paintEvent(self, e):
        p = QPainter(self)
        theme.draw_bg(p, self.rect(), float(self.cfg.get("ui.darkness", 0.85)),
                      grid=self.cfg.get("ui.mode") == "cinematic")
        if self.cfg.get("ui.mode") == "cinematic":
            p.setPen(QPen(QColor(41, 216, 255, 40), 1))
            y = int((time.monotonic() * 40 * self.cfg.get("ui.animation_speed", 1)) % max(1, self.height()))
            p.drawLine(0, y, self.width(), y)   # scan line

    def poll_core(self):
        st = self.core.call("status") or {}
        tel = st.get("telemetry") or {}
        if tel:
            self.g_cpu.set(tel.get("cpu", 0) / 100, f"{tel.get('cpu', 0):.0f}%")
            self.g_ram.set(tel.get("ram_pct", 0) / 100, f"{tel.get('ram_pct', 0):.0f}%")
            self.g_disk.set(tel.get("disk_pct", 0) / 100, f"{tel.get('disk_pct', 0):.0f}%")
            lines = [f"RAM   {tel.get('ram_avail_mb', '?')} MB free / {tel.get('ram_total_mb', '?')} MB",
                     f"DISK  {tel.get('disk_free_gb', '?')} GB free"]
            if "net_rx" in tel:
                if self.prev_net:
                    rx = (tel["net_rx"] - self.prev_net[0])
                    tx = (tel["net_tx"] - self.prev_net[1])
                    lines.append(f"NET   {'UP' if tel.get('net_up') else 'DOWN'}  ↓{fmt_bytes(max(rx,0))}/s ↑{fmt_bytes(max(tx,0))}/s")
                self.prev_net = (tel["net_rx"], tel["net_tx"])
            if "battery" in tel:
                lines.append(f"BATT  {tel['battery']}% {'⚡' if tel.get('charging') else ''}")
            if "temp_c" in tel:
                lines.append(f"TEMP  {tel['temp_c']}°C")
            lines.append(f"LOAD  {st.get('load', 0) * 100:.0f}% pressure")
            self.lbl_sys.setText("\n".join(lines))
        online = self.core.online
        healthy = online and not st.get("stop")
        self.lbl_health.setText(f"<span style='color:{OK if healthy else WARN if online else DANGER}'>● "
                                f"{'CORE ONLINE' if online else 'CORE OFFLINE — desktop still usable'}</span>")
        sens = st.get("sensors", {})
        prov = st.get("providers", [])
        def dot(s):
            c = OK if str(s).startswith("running") else DIM if s in ("off", None) else WARN
            return f"<span style='color:{c}'>●</span>"
        ptxt = ", ".join(f"{dot('running' if x.get('has_key') and x.get('health', {}).get('ok', True) else 'err')} {x['name']}"
                         for x in prov) or f"<span style='color:{DIM}'>no AI provider — offline commands only</span>"
        self.lbl_sensors.setText(
            f"{dot(sens.get('hand_tracking', 'off'))} Hand tracking: {sens.get('hand_tracking', 'off')}<br>"
            f"{dot(sens.get('clap', 'off'))} Clap: {sens.get('clap', 'off')}<br>"
            f"{dot(sens.get('voice', 'off'))} Voice: {sens.get('voice', 'off')}<br>"
            f"AI: {ptxt}<br>MCP: {', '.join(f'{k}: {v}' for k, v in st.get('mcp', {}).items()) or '—'}")
        if st.get("mode") and st.get("mode") != self.cfg.get("control.mode"):
            self.refresh_config()
        # active window / workspace
        try:
            from ..computer_control.system import active_window
            self.lbl_active.setText(active_window().get("title") or "Desktop")
        except Exception:
            pass
        self.lbl_recent.setText("\n".join(Path(p).name for p in recent_files()) or "—")
        # events
        for ev in self.core.call("events", since=self.last_event_ts) or []:
            self.last_event_ts = max(self.last_event_ts, ev["ts"])
            self.on_event(ev)
        tasks = self.core.call("tasks") or []
        self.lbl_tasks.setText("<br>".join(
            f"<span style='color:{self._status_col(t['status'])}'>●</span> {t['goal'][:38]} "
            f"<span style='color:{DIM}'>{t['progress'][0]}/{t['progress'][1]} {t['status']}</span>"
            for t in tasks[-6:][::-1]) or "No tasks yet.")
        for req in self.core.call("approvals") or []:
            if req["id"] not in self.shown_approvals:
                self.shown_approvals.add(req["id"])
                self.reactor.state = "alert"
                if not self.isVisible():
                    self.show()
                dlg = ApprovalDialog(req, self)
                dlg.exec_()
                self.core.call("approve", id=req["id"], status=dlg.result_status)
                if dlg.result_status == "stop":
                    self.emergency_stop()

    @staticmethod
    def _status_col(s):
        return {"completed": OK, "failed": DANGER, "cancelled": WARN, "running": CYAN, "needs_user": VIOLET}.get(s, DIM)

    def on_event(self, ev):
        e = ev["event"]
        if e == "task":
            self.current_task = ev
            st = ev["status"]
            self.reactor.state = {"running": "working", "planning": "thinking", "failed": "alert",
                                  "cancelled": "stopped"}.get(st, "idle")
            self.wave.level = 0.9 if st == "running" else 0.15
            done, total = ev["progress"]
            self.task_bar.setMaximum(max(total, 1))
            self.task_bar.setValue(done)
            steps = "<br>".join(f"<span style='color:{self._status_col({'done': 'completed', 'failed': 'failed', 'running': 'running'}.get(s['status'], ''))}'>"
                                f"{'✔' if s['status'] == 'done' else '✘' if s['status'] in ('failed', 'denied') else '…' if s['status'] == 'running' else '·'}"
                                f"</span> {s['desc']}" for s in ev["steps"][:8])
            err = f"<br><span style='color:{DANGER}'>{ev['error'][:200]}</span>" if ev.get("error") else ""
            self.lbl_task.setText(f"<b>{ev['goal']}</b> — <span style='color:{self._status_col(st)}'>{st.upper()}</span><br>{steps}{err}")
            if st in ("completed", "failed", "cancelled", "needs_user"):
                self.notify(f"{st}: {ev['goal'][:50]}")
        elif e == "config":
            self.refresh_config()
        elif e == "notify":
            self.notify(ev.get("text", ""))
        elif e == "stop":
            self.reactor.state = "stopped"
            self.notify("EMERGENCY STOP: " + ev.get("reason", ""))
        elif e == "gesture":
            self.notify(f"gesture: {ev['gesture']}")
        elif e == "clap":
            self.notify(f"{'👏' * ev['count']} clap x{ev['count']}")
        elif e == "ui":
            cmd = ev.get("command")
            if cmd == "show_hud":
                self.show_hud()
            elif cmd == "hide_hud":
                self.hide_hud()
            elif cmd == "voice":
                self.voice()
        elif e == "sensor" and ev["status"].startswith(("error", "unavailable")):
            self.notify(f"{ev['name']}: {ev['status']}")

    def send_command(self):
        text = self.cmd.text().strip()
        if not text:
            return
        self.cmd.clear()
        self.reactor.state = "thinking"
        r = self.core.call("command", text=text)
        if isinstance(r, dict) and r.get("error"):
            # core offline: handle launch locally so the desktop remains usable
            if text.lower().startswith(("open ", "launch ")):
                self.launch(text.split(" ", 1)[1])
            else:
                self.notify(r["error"])
            self.reactor.state = "alert"

    def voice(self):
        self.reactor.state = "listening"
        self.wave.level = 1.0
        import subprocess
        subprocess.Popen([sys.executable, "-m", "jarvis.cli", "voice"], start_new_session=True)

    def emergency_stop(self):
        self.core.call("stop", reason="STOP button")
        import subprocess
        subprocess.Popen(["pkill", "-f", "xdotool"], stderr=subprocess.DEVNULL)
        self.reactor.state = "stopped"
        self.notify("EMERGENCY STOP")

    def toggle_mode(self):
        new = "performance" if self.cfg.get("ui.mode") == "cinematic" else "cinematic"
        self.core.call("config.mode", mode=new)
        self.refresh_config()

    def open_launcher(self):
        Launcher(self.core, self).exec_()

    def show_hud(self):
        self.showFullScreen() if os.environ.get("JARVIS_HUD_FULLSCREEN", "1") == "1" else self.show()
        self.raise_()
        self.activateWindow()
        self.cmd.setFocus()

    def hide_hud(self):
        self.hide()

    def on_ipc(self):
        sock = self.ipc.nextPendingConnection()
        def read():
            cmd = bytes(sock.readAll()).decode().strip()
            if cmd == "command":
                self.show_hud()
            elif cmd == "toggle":
                self.hide_hud() if self.isVisible() else self.show_hud()
            elif cmd == "launcher":
                self.open_launcher()
            elif cmd == "voice":
                self.voice()
            elif cmd == "stop":
                self.emergency_stop()
            sock.disconnectFromServer()
        sock.readyRead.connect(read)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.emergency_stop()
        elif e.key() == Qt.Key_A and e.modifiers() & Qt.MetaModifier:
            self.open_launcher()
        else:
            super().keyPressEvent(e)


def main(argv=None):
    app = theme.ensure_app(sys.argv)
    app.setApplicationName("JarvisHUD")
    app.setDesktopFileName("jarvis-hud")
    hud = HUD()
    theme.apply(app, lambda k: hud.cfg.get(k))
    if "--window" in (argv or sys.argv):
        hud.resize(1366, 768)
        hud.show()
    else:
        hud.show_hud()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
