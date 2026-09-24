"""JARVIS Settings — every schema setting is editable here AND via natural language,
because both go through the same validated config API."""
from __future__ import annotations

import json
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QFormLayout, QCheckBox, QSlider,
                             QComboBox, QLineEdit, QLabel, QScrollArea, QTableWidget, QTableWidgetItem,
                             QPlainTextEdit, QMessageBox, QInputDialog, QHeaderView)

from . import theme
from .theme import Panel, button
from .core_link import CoreLink
from ..core.config import SCHEMA, GESTURE_NAMES

TABS = [
    ("Interface", "ui."), ("Control", "control."), ("Vision && Gestures", "vision."), ("Audio && Voice", "audio."),
    ("AI Routing", "ai."), ("Hotkeys", "hotkeys."), ("Privacy", ("memory.", "audit.")), ("Performance", "perf."),
]


class SettingsWindow(QWidget):
    def __init__(self, core: CoreLink | None = None):
        super().__init__()
        self.core = core or CoreLink()
        self.setObjectName("root")
        self.setWindowTitle("JARVIS Settings")
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        t = QLabel("<span style='color:%s;letter-spacing:5px;font-size:18px;font-weight:bold'>SETTINGS</span>" % theme.CYAN)
        head.addWidget(t)
        head.addStretch()
        head.addWidget(button("PERFORMANCE MODE", "", "Apply performance preset", lambda: self._mode("performance")))
        head.addWidget(button("CINEMATIC MODE", "", "Apply cinematic preset", lambda: self._mode("cinematic")))
        lay.addLayout(head)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self.status)
        self.widgets = {}
        self.reload()

    def reload(self):
        self.data = self.core.call("config.get") or {"settings": {}}
        self.tabs.clear()
        for name, prefix in TABS:
            self.tabs.addTab(self._settings_tab(prefix), name)
        self.tabs.insertTab(3, self._mappings_tab(), "Gesture && Clap Mappings")
        self.tabs.addTab(self._providers_tab(), "AI Providers")
        self.tabs.addTab(self._mcp_tab(), "MCP")
        self.tabs.addTab(self._perm_tab(), "Permissions")
        self.tabs.addTab(self._audit_tab(), "Audit Log")

    def _mode(self, m):
        self.core.call("config.mode", mode=m)
        self.reload()
        self.status.setText(f"{m} mode applied")

    def _set(self, key, value):
        r = self.core.call("config.set", key=key, value=value)
        ok = not (isinstance(r, dict) and r.get("ok") is False)
        self.status.setText(f"✔ {key} = {value}" if ok else f"✘ {key}: {r.get('error')}")

    def _settings_tab(self, prefix):
        w = QWidget()
        f = QFormLayout(w)
        f.setLabelAlignment(Qt.AlignRight)
        prefixes = prefix if isinstance(prefix, tuple) else (prefix,)
        vals = self.data.get("settings", {})
        for key, s in SCHEMA.items():
            if not key.startswith(prefixes):
                continue
            v = vals.get(key, s.default)
            if s.type is bool:
                ed = QCheckBox("on")
                ed.setChecked(bool(v))
                ed.toggled.connect(lambda val, k=key: self._set(k, val))
            elif s.choices:
                ed = QComboBox()
                ed.addItems(list(s.choices))
                ed.setCurrentText(str(v))
                ed.currentTextChanged.connect(lambda val, k=key: self._set(k, val))
            elif s.type in (int, float) and s.min is not None and s.max is not None:
                ed = QWidget()
                h = QHBoxLayout(ed)
                h.setContentsMargins(0, 0, 0, 0)
                sl = QSlider(Qt.Horizontal)
                scale = 1 if s.type is int else 100
                sl.setRange(int(s.min * scale), int(s.max * scale))
                sl.setValue(int(v * scale))
                lbl = QLabel(str(v))
                lbl.setMinimumWidth(56)
                sl.valueChanged.connect(lambda x, l=lbl, sc=scale, t=s.type: l.setText(str(x if t is int else x / sc)))
                sl.sliderReleased.connect(lambda k=key, sl=sl, sc=scale, t=s.type: self._set(k, sl.value() if t is int else sl.value() / sc))
                sl.setAccessibleName(key)
                h.addWidget(sl)
                h.addWidget(lbl)
            else:
                ed = QLineEdit(str(v))
                ed.editingFinished.connect(lambda k=key, e=ed: self._set(k, e.text()))
            ed.setToolTip(s.description or key)
            label = QLabel(key.split(".", 1)[1].replace("_", " ").title() + (" ⚠" if s.sensitive else ""))
            label.setToolTip(s.description)
            f.addRow(label, ed)
        sc = QScrollArea()
        sc.setWidget(w)
        sc.setWidgetResizable(True)
        return sc

    def _mappings_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Tip: you can also just tell JARVIS — “when I show two fingers, open Blender”."))
        self.gt = QTableWidget(0, 5)
        self.gt.setHorizontalHeaderLabels(["Gesture", "Action (JSON)", "Enabled", "Confidence", "Cooldown ms"])
        self.gt.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for g in self.data.get("gestures", []):
            r = self.gt.rowCount()
            self.gt.insertRow(r)
            for c, v in enumerate([g["gesture"], json.dumps(g["action"]), "yes" if g["enabled"] else "no",
                                   str(g["confidence_threshold"]), str(g["cooldown_ms"])]):
                self.gt.setItem(r, c, QTableWidgetItem(v))
        lay.addWidget(Panel("Hand gestures"))
        lay.addWidget(self.gt)
        self.ct = QTableWidget(0, 3)
        self.ct.setHorizontalHeaderLabels(["Claps", "Action (JSON)", "Enabled"])
        self.ct.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c in self.data.get("claps", []):
            r = self.ct.rowCount()
            self.ct.insertRow(r)
            for i, v in enumerate(["👏" * c["count"] + f" ({c['count']})", json.dumps(c["action"]), "yes" if c["enabled"] else "no"]):
                self.ct.setItem(r, i, QTableWidgetItem(v))
        lay.addWidget(Panel("Clap sequences"))
        lay.addWidget(self.ct)
        row = QHBoxLayout()
        row.addWidget(button("ADD GESTURE ROW", "", "", lambda: self.gt.insertRow(self.gt.rowCount())))
        row.addWidget(button("ADD CLAP ROW", "", "", lambda: self.ct.insertRow(self.ct.rowCount())))
        row.addStretch()
        row.addWidget(button("SAVE MAPPINGS", "ok", "Validate and save", self._save_maps))
        lay.addLayout(row)
        return w

    def _save_maps(self):
        try:
            gs = []
            for r in range(self.gt.rowCount()):
                cell = lambda c: (self.gt.item(r, c).text() if self.gt.item(r, c) else "")
                if not cell(0):
                    continue
                gs.append({"gesture": cell(0), "action": json.loads(cell(1) or '{"type":"NONE"}'),
                           "enabled": cell(2) != "no", "confidence_threshold": float(cell(3) or 0.75),
                           "cooldown_ms": int(cell(4) or 1000)})
            cs = []
            for r in range(self.ct.rowCount()):
                cell = lambda c: (self.ct.item(r, c).text() if self.ct.item(r, c) else "")
                if not cell(0):
                    continue
                n = int("".join(ch for ch in cell(0) if ch.isdigit()) or cell(0).count("👏"))
                cs.append({"count": n, "action": json.loads(cell(1) or '{"type":"NONE"}'), "enabled": cell(2) != "no"})
            self.core.call("config.structured", key="gestures", value=gs)
            self.core.call("config.structured", key="claps", value=cs)
            self.status.setText("✔ mappings saved")
        except Exception as e:
            QMessageBox.warning(self, "Invalid mapping", str(e))

    def _providers_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["Name", "Adapter", "Base URL", "Models", "Key"])
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for p in self.core.call("providers") or []:
            r = t.rowCount()
            t.insertRow(r)
            for c, v in enumerate([p["name"], p["adapter"], p["base_url"], json.dumps(p["models"]), "✔" if p["has_key"] else "✘"]):
                t.setItem(r, c, QTableWidgetItem(v))
        lay.addWidget(t)
        form = QFormLayout()
        self.pv = {k: QLineEdit() for k in ("name", "template", "base_url", "models", "api_key")}
        self.pv["api_key"].setEchoMode(QLineEdit.Password)
        self.pv["template"].setPlaceholderText("gemini | openrouter | mistral | huggingface | openai | ollama | llamacpp | (blank=custom)")
        self.pv["models"].setPlaceholderText('{"text": "model-name", "vision": "...", "coding": "..."}')
        for k, e in self.pv.items():
            form.addRow(k.replace("_", " ").title(), e)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("ADD / UPDATE PROVIDER", "ok", "", self._add_provider))
        row.addWidget(button("TEST SELECTED", "", "", lambda: self._test_provider(t)))
        row.addStretch()
        lay.addLayout(row)
        return w

    def _add_provider(self):
        v = {k: e.text().strip() for k, e in self.pv.items()}
        params = {"name": v["name"], "template": v["template"] or None, "api_key": v["api_key"] or None}
        if v["base_url"]:
            params["base_url"] = v["base_url"]
        if v["models"]:
            params["models"] = json.loads(v["models"])
        r = self.core.call("provider.add", **params)
        self.pv["api_key"].clear()
        self.status.setText(str(r))

    def _test_provider(self, t):
        it = t.item(t.currentRow(), 0)
        if it:
            self.status.setText(json.dumps(self.core.call("provider.test", name=it.text()))[:200])

    def _mcp_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        d = self.core.call("mcp.list") or {"servers": {}, "tools": {}}
        txt = "\n".join(f"● {n}: {s['status']} ({'enabled' if s['enabled'] else 'disabled'})" for n, s in d["servers"].items())
        txt += "\n\nTOOLS\n" + "\n".join(f"  {k:40} {t['op']:13} risk={t['risk']:8} {'on' if t['enabled'] else 'off'}"
                                          for k, t in d["tools"].items())
        e = QPlainTextEdit(txt or "No MCP servers. Add one with:\n  jarvis mcp add github --command 'npx -y @modelcontextprotocol/server-github'")
        e.setReadOnly(True)
        e.setFont(theme.QFont(theme.FONT_MONO, 10))
        lay.addWidget(e)
        return w

    def _perm_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        for name, granted in (self.core.call("permissions") or {}).items():
            cb = QCheckBox("granted (otherwise JARVIS asks)")
            cb.setChecked(granted)
            cb.toggled.connect(lambda v, n=name: self.core.call("permission.set", name=n, value=v))
            f.addRow(name.replace("_", " ").title(), cb)
        f.addRow(QLabel("Critical operations (disks, EFI, bootloader, auth, security) ALWAYS require confirmation."))
        return w

    def _audit_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        q = QLineEdit()
        q.setPlaceholderText("Search audit log…")
        e = QPlainTextEdit()
        e.setReadOnly(True)
        e.setFont(theme.QFont(theme.FONT_MONO, 9))
        def load():
            es = self.core.call("audit", limit=300, query=q.text() or None) or []
            e.setPlainText("\n".join(f"{x['time']} {x['category']:10} {x['message']}" for x in es))
        q.returnPressed.connect(load)
        lay.addWidget(q)
        lay.addWidget(e)
        load()
        return w


def build_for_screenshot():
    return SettingsWindow()


def main(argv=None):
    app = theme.ensure_app(sys.argv)
    w = SettingsWindow()
    theme.apply(app, lambda k: w.data.get("settings", {}).get(k))
    w.resize(1100, 720)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
