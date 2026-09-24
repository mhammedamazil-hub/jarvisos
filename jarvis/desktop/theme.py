"""Shared JARVIS visual language for every desktop component (Qt5)."""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QLinearGradient, QBrush, QPainterPath
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget, QPushButton

CYAN = "#29d8ff"
BLUE = "#1f6fff"
VIOLET = "#9b6bff"
WHITE = "#e8f7ff"
DIM = "#6f93a8"
BG = "#03080f"
PANEL = "#07141f"
WARN = "#ffb547"
DANGER = "#ff4d6d"
OK = "#3dffa8"

MODE_COLORS = {"safe": OK, "assisted": CYAN, "autonomous": WARN, "full": DANGER}
FONT_UI = "DejaVu Sans"
FONT_MONO = "DejaVu Sans Mono"


def qss(accent: str = CYAN, alpha: float = 0.92, high_contrast: bool = False) -> str:
    a = int(255 * alpha)
    border = "#ffffff" if high_contrast else accent
    return f"""
    * {{ font-family: '{FONT_UI}'; color: {WHITE}; }}
    QWidget {{ background: transparent; font-size: 13px; }}
    QMainWindow, QDialog, #root {{ background: {BG}; }}
    QFrame#panel {{ background: rgba(7, 20, 31, {a}); border: 1px solid rgba(41,216,255,0.35);
                    border-radius: 10px; }}
    QLabel#title {{ color: {accent}; font-size: 11px; letter-spacing: 3px; font-weight: bold; }}
    QLabel#big {{ font-family: '{FONT_MONO}'; font-size: 26px; color: {WHITE}; }}
    QLabel#dim {{ color: {DIM}; font-size: 11px; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeView,
    QTableWidget, QListView {{
        background: rgba(3, 12, 20, 230); border: 1px solid rgba(41,216,255,0.35); border-radius: 6px;
        padding: 6px; selection-background-color: rgba(41,216,255,0.35); selection-color: white; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QListWidget:focus, QTreeView:focus {{
        border: 1px solid {border}; }}
    QPushButton {{ background: rgba(41,216,255,0.10); border: 1px solid rgba(41,216,255,0.55); border-radius: 6px;
                   padding: 7px 14px; color: {WHITE}; font-weight: bold; letter-spacing: 1px; }}
    QPushButton:hover {{ background: rgba(41,216,255,0.25); }}
    QPushButton:focus {{ border: 2px solid {border}; }}
    QPushButton:pressed {{ background: rgba(41,216,255,0.4); }}
    QPushButton#danger {{ background: rgba(255,77,109,0.18); border-color: {DANGER}; color: #ffd6de; }}
    QPushButton#danger:hover {{ background: rgba(255,77,109,0.4); }}
    QPushButton#ok {{ background: rgba(61,255,168,0.15); border-color: {OK}; }}
    QPushButton#ghost {{ background: transparent; border: none; color: {DIM}; }}
    QTabWidget::pane {{ border: 1px solid rgba(41,216,255,0.3); border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ background: transparent; padding: 9px 12px; color: {DIM}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
    QTabBar::tab:focus {{ color: white; }}
    QSlider::groove:horizontal {{ height: 4px; background: rgba(41,216,255,0.2); border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {WHITE}; border: 2px solid {accent}; width: 14px; margin: -6px 0;
                                  border-radius: 8px; }}
    QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px; border: 1px solid {accent}; }}
    QCheckBox::indicator:checked {{ background: {accent}; }}
    QCheckBox:focus {{ color: {accent}; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: rgba(41,216,255,0.35); border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QHeaderView::section {{ background: rgba(7,20,31,240); color: {accent}; border: none; padding: 6px;
                            border-bottom: 1px solid rgba(41,216,255,0.3); }}
    QMenu {{ background: {PANEL}; border: 1px solid {accent}; }}
    QMenu::item:selected {{ background: rgba(41,216,255,0.3); }}
    QToolTip {{ background: {PANEL}; color: white; border: 1px solid {accent}; }}
    QProgressBar {{ border: 1px solid rgba(41,216,255,0.4); border-radius: 4px; height: 8px; text-align: center;
                    background: rgba(3,12,20,200); font-size: 9px; }}
    QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {BLUE}, stop:1 {accent});
                           border-radius: 3px; }}
    """


def apply(app: QApplication, cfg_get=None):
    accent = CYAN
    hc = False
    if cfg_get:
        try:
            accent = cfg_get("ui.accent") or CYAN
            hc = bool(cfg_get("ui.high_contrast"))
        except Exception:
            pass
    app.setStyleSheet(qss(accent, high_contrast=hc))
    f = QFont(FONT_UI, 10)
    f.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(f)


class Panel(QFrame):
    """Holographic glass panel with a HUD title and corner brackets."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 12)
        self.lay.setSpacing(6)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("title")
            t.setAccessibleName(title)
            self.lay.addWidget(t)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(CYAN), 2))
        r = self.rect().adjusted(1, 1, -2, -2)
        L = 12
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(x, y + dy * 6, x, y + dy * L)
            p.drawLine(x + dx * 6, y, x + dx * L, y)


def button(text: str, kind: str = "", tip: str = "", cb=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    b.setAccessibleName(tip or text)
    b.setCursor(Qt.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


def draw_bg(p: QPainter, rect, darkness: float = 0.85, grid: bool = True):
    g = QLinearGradient(0, 0, 0, rect.height())
    base = int(18 * (1 - darkness)) + 2
    g.setColorAt(0, QColor(base, base + 8, base + 16))
    g.setColorAt(1, QColor(1, 3, 7))
    p.fillRect(rect, QBrush(g))
    if grid:
        p.setPen(QPen(QColor(41, 216, 255, 12), 1))
        step = 40
        for x in range(0, rect.width(), step):
            p.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), step):
            p.drawLine(0, y, rect.width(), y)


def ensure_app(argv=None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        app = QApplication(argv or ["jarvis"])
    return app
