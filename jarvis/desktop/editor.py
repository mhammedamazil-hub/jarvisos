"""JARVIS text editor: line numbers, syntax highlighting, search/replace, unsaved-change
protection, and root-file editing with approval + automatic backup."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QRect, QSize, QRegularExpression
from PyQt5.QtGui import QColor, QPainter, QFont, QSyntaxHighlighter, QTextCharFormat, QKeySequence, QTextDocument
from PyQt5.QtWidgets import (QWidget, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QFileDialog,
                             QMessageBox, QShortcut)

from . import theme
from .theme import button

KEYWORDS = {
    ".py": r"\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|lambda|yield|None|True|False|and|or|not|in|is|pass|break|continue|raise|async|await|global)\b",
    ".sh": r"\b(if|then|else|fi|for|do|done|while|case|esac|function|echo|export|local|return|in)\b",
    ".js": r"\b(function|const|let|var|return|if|else|for|while|class|import|export|from|new|async|await|null|true|false)\b",
    ".conf": r"^\s*[\w.\-]+(?=\s*[=:])",
}
KEYWORDS[".ini"] = KEYWORDS[".toml"] = KEYWORDS[".cfg"] = KEYWORDS[".conf"]
KEYWORDS[".ts"] = KEYWORDS[".js"]


class Highlighter(QSyntaxHighlighter):
    def __init__(self, doc, suffix):
        super().__init__(doc)
        def fmt(c, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(c))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f
        self.rules = []
        if suffix in KEYWORDS:
            self.rules.append((QRegularExpression(KEYWORDS[suffix]), fmt(theme.CYAN, True)))
        self.rules += [(QRegularExpression(r"\"[^\"\n]*\"|'[^'\n]*'"), fmt("#ffcf7a")),
                       (QRegularExpression(r"\b\d+(\.\d+)?\b"), fmt(theme.VIOLET)),
                       (QRegularExpression(r"(#|//).*$|^\s*;.*$"), fmt(theme.DIM))]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class LineNumbers(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), QColor(4, 12, 20))
        b = self.ed.firstVisibleBlock()
        top = int(self.ed.blockBoundingGeometry(b).translated(self.ed.contentOffset()).top())
        cur = self.ed.textCursor().blockNumber()
        while b.isValid() and top <= e.rect().bottom():
            if b.isVisible():
                p.setPen(QColor(theme.CYAN if b.blockNumber() == cur else theme.DIM))
                p.drawText(0, top, self.width() - 6, self.ed.fontMetrics().height(), Qt.AlignRight, str(b.blockNumber() + 1))
            top += int(self.ed.blockBoundingRect(b).height())
            b = b.next()


class CodeEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.nums = LineNumbers(self)
        self.setFont(QFont(theme.FONT_MONO, 11))
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(lambda r, dy: self.nums.scroll(0, dy) if dy else self.nums.update(0, r.y(), self.nums.width(), r.height()))
        self.cursorPositionChanged.connect(self.nums.update)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.setStyleSheet("QPlainTextEdit{border:none;border-radius:0}")

    def gutter_width(self):
        return 16 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.nums.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))


class Editor(QWidget):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.setObjectName("root")
        self.path = None
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.title = QLabel()
        bar.addWidget(self.title)
        bar.addStretch()
        for t, cb, key in (("OPEN", self.open_dialog, "Ctrl+O"), ("SAVE", self.save, "Ctrl+S"),
                           ("SAVE AS", self.save_as, "Ctrl+Shift+S"), ("FIND", self.toggle_find, "Ctrl+F")):
            bar.addWidget(button(t, "ghost", f"{t.title()} ({key})", cb))
            QShortcut(QKeySequence(key), self, cb)
        lay.addLayout(bar)
        self.find_bar = QWidget()
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.q, self.r = QLineEdit(), QLineEdit()
        self.q.setPlaceholderText("Find")
        self.r.setPlaceholderText("Replace with")
        self.q.returnPressed.connect(self.find_next)
        fb.addWidget(self.q)
        fb.addWidget(self.r)
        fb.addWidget(button("NEXT", "", "", self.find_next))
        fb.addWidget(button("REPLACE ALL", "", "", self.replace_all))
        self.find_bar.hide()
        lay.addWidget(self.find_bar)
        panel = theme.Panel("")
        self.ed = CodeEdit()
        panel.lay.addWidget(self.ed)
        lay.addWidget(panel, 1)
        self.status = QLabel()
        self.status.setObjectName("dim")
        lay.addWidget(self.status)
        self.ed.document().modificationChanged.connect(lambda _: self._title())
        self.ed.cursorPositionChanged.connect(self._pos)
        if path:
            self.load(path)
        self._title()

    def _title(self):
        name = self.path.name if self.path else "untitled"
        mod = " •" if self.ed.document().isModified() else ""
        root = "  <span style='color:#ffb547'>[ROOT FILE — saving needs approval]</span>" if self.is_root() else ""
        self.title.setText(f"<span style='color:{theme.CYAN};font-weight:bold;letter-spacing:2px'>{name}{mod}</span>"
                           f"<span style='color:{theme.DIM}'>  {self.path or ''}</span>{root}")
        self.setWindowTitle(f"{name}{mod} — JARVIS Editor")

    def _pos(self):
        c = self.ed.textCursor()
        self.status.setText(f"Ln {c.blockNumber() + 1}, Col {c.columnNumber() + 1}   {self.ed.blockCount()} lines")

    def is_root(self):
        return bool(self.path) and not os.access(self.path if self.path.exists() else self.path.parent, os.W_OK)

    def load(self, path):
        p = Path(path).expanduser()
        try:
            text = p.read_text(errors="replace") if p.exists() else ""
        except PermissionError:
            r = subprocess.run(["pkexec", "cat", str(p)], capture_output=True, text=True)
            if r.returncode:
                QMessageBox.warning(self, "Permission denied", f"Cannot read {p}")
                return
            text = r.stdout
        self.path = p
        self.ed.setPlainText(text)
        self.hl = Highlighter(self.ed.document(), p.suffix or (".sh" if text.startswith("#!") else ".conf" if str(p).startswith("/etc") else ""))
        self.ed.document().setModified(False)
        self._title()

    def open_dialog(self):
        if not self.confirm_discard():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Open file", str(Path.home()))
        if f:
            self.load(f)

    def save_as(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save as", str(self.path or Path.home()))
        if f:
            self.path = Path(f)
            self.save()

    def save(self):
        if not self.path:
            return self.save_as()
        text = self.ed.toPlainText()
        from ..computer_control.system import backup_file
        if self.is_root():
            ok = QMessageBox.question(self, "JARVIS requests approval",
                                      f"Edit root-owned file?\n\n{self.path}\n\nRisk: HIGH · Reversible: yes (backup created first)\n"
                                      "You will be asked for your password.") == QMessageBox.Yes
            if not ok:
                return
            bak = backup_file(str(self.path))
            r = subprocess.run(["pkexec", "tee", str(self.path)], input=text, text=True, capture_output=True)
            if r.returncode:
                QMessageBox.warning(self, "Save failed", r.stderr or "authorization failed")
                return
            self.status.setText(f"saved with root · backup: {bak}")
        else:
            bak = backup_file(str(self.path)) if self.path.exists() else None
            self.path.write_text(text)
            self.status.setText(f"saved · backup: {bak or 'n/a (new file)'}")
        self.ed.document().setModified(False)
        self._title()

    def toggle_find(self):
        self.find_bar.setVisible(not self.find_bar.isVisible())
        if self.find_bar.isVisible():
            self.q.setFocus()

    def find_next(self):
        if not self.ed.find(self.q.text()):
            c = self.ed.textCursor()
            c.movePosition(c.Start)
            self.ed.setTextCursor(c)
            self.ed.find(self.q.text())

    def replace_all(self):
        q = self.q.text()
        if q:
            t = self.ed.toPlainText()
            n = t.count(q)
            c = self.ed.textCursor()
            c.select(c.Document)
            c.insertText(t.replace(q, self.r.text()))
            self.status.setText(f"replaced {n} occurrence(s)")

    def confirm_discard(self):
        if not self.ed.document().isModified():
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes before continuing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            self.save()
            return not self.ed.document().isModified()
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        e.accept() if self.confirm_discard() else e.ignore()


def build_for_screenshot():
    e = Editor(__file__)
    return e


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    app = theme.ensure_app(sys.argv)
    theme.apply(app)
    w = Editor(argv[0] if argv else None)
    w.resize(1000, 700)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
