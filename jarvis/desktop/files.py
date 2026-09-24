"""JARVIS file manager: tabs, favorites, search, hidden files, preview, info/permissions,
copy/cut/paste, rename, trash-based delete with undo, root prompts, storage usage."""
from __future__ import annotations

import mimetypes
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QDir, QModelIndex, QSortFilterProxyModel
from PyQt5.QtGui import QKeySequence, QPixmap, QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeView, QFileSystemModel, QLineEdit, QLabel,
                             QListWidget, QTabWidget, QSplitter, QMenu, QInputDialog, QMessageBox, QShortcut,
                             QProgressBar, QAbstractItemView)

from . import theme
from .theme import button, Panel
from ..computer_control.system import trash

TEXT_EXT = {".txt", ".md", ".py", ".sh", ".conf", ".cfg", ".ini", ".json", ".toml", ".yaml", ".yml", ".log",
            ".js", ".ts", ".c", ".h", ".cpp", ".rs", ".go", ".html", ".css", ".xml", ".desktop", ".service"}


def open_path(p: Path):
    if p.suffix.lower() in TEXT_EXT or (p.stat().st_size < 2_000_000 and _is_text(p)):
        subprocess.Popen([sys.executable, "-m", "jarvis.desktop.editor", str(p)], start_new_session=True)
    else:
        subprocess.Popen(["xdg-open", str(p)], start_new_session=True, stderr=subprocess.DEVNULL)


def _is_text(p: Path) -> bool:
    try:
        return b"\0" not in p.open("rb").read(2048)
    except OSError:
        return False


class Pane(QWidget):
    def __init__(self, owner, start: Path):
        super().__init__()
        self.owner = owner
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        nav = QHBoxLayout()
        nav.addWidget(button("←", "ghost", "Back (Alt+Left)", self.back))
        nav.addWidget(button("↑", "ghost", "Parent folder (Backspace)", self.up))
        self.addr = QLineEdit()
        self.addr.setAccessibleName("path")
        self.addr.returnPressed.connect(lambda: self.go(Path(self.addr.text()).expanduser()))
        nav.addWidget(self.addr, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter / search…")
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self.filter)
        nav.addWidget(self.search)
        lay.addLayout(nav)
        self.model = QFileSystemModel()
        self.model.setRootPath("/")
        self.model.setReadOnly(True)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setColumnWidth(0, 320)
        self.view.setAlternatingRowColors(False)
        self.view.doubleClicked.connect(self.activate)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.menu)
        self.view.selectionModel().currentChanged.connect(lambda cur, _: owner.preview(self.path_of(cur)))
        lay.addWidget(self.view)
        self.history = []
        self.go(start)

    def path_of(self, idx: QModelIndex) -> Path:
        return Path(self.model.filePath(idx))

    def cwd(self) -> Path:
        return Path(self.model.filePath(self.view.rootIndex()))

    def go(self, p: Path, record=True):
        if not p.is_dir():
            return
        if not os.access(p, os.R_OK | os.X_OK):
            QMessageBox.information(self, "Root access", f"{p} requires administrator rights.\n"
                                    "Open it from a root terminal or ask JARVIS (approval will be requested).")
            return
        if record and self.history[-1:] != [p]:
            self.history.append(p)
        self.view.setRootIndex(self.model.index(str(p)))
        self.addr.setText(str(p))
        self.owner.tabs.setTabText(self.owner.tabs.indexOf(self), p.name or "/")
        self.owner.update_storage(p)

    def back(self):
        if len(self.history) > 1:
            self.history.pop()
            self.go(self.history[-1], record=False)

    def up(self):
        self.go(self.cwd().parent)

    def activate(self, idx):
        p = self.path_of(idx)
        self.go(p) if p.is_dir() else open_path(p)

    def filter(self, text):
        self.model.setNameFilters([f"*{text}*"] if text else [])
        self.model.setNameFilterDisables(False)

    def selected(self) -> list[Path]:
        return [self.path_of(i) for i in self.view.selectionModel().selectedRows()]

    def menu(self, pos):
        m = QMenu(self)
        o = self.owner
        for text, cb in (("Open", lambda: [open_path(p) if p.is_file() else self.go(p) for p in self.selected()]),
                         ("Open in terminal", lambda: subprocess.Popen(["x-terminal-emulator"], cwd=str(self.cwd()))),
                         ("New folder", o.new_folder), ("Rename", o.rename), ("Copy", o.copy), ("Cut", o.cut),
                         ("Paste", o.paste), ("Move to trash", o.delete), ("Add to favorites", o.add_fav),
                         ("Properties", lambda: o.preview(self.selected()[0] if self.selected() else self.cwd(), True))):
            m.addAction(text, cb)
        m.exec_(self.view.viewport().mapToGlobal(pos))


class FileManager(QWidget):
    def __init__(self, start: str | None = None):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("JARVIS Files")
        self.clip: tuple[str, list[Path]] | None = None
        self.undo: list[dict] = []
        self.show_hidden = False
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<span style='color:{theme.CYAN};letter-spacing:5px;font-size:17px;font-weight:bold'>FILES</span>"))
        head.addStretch()
        for t, tip, cb in (("+ TAB", "New tab (Ctrl+T)", lambda: self.new_tab(Path.home())),
                           ("NEW FOLDER", "Ctrl+Shift+N", self.new_folder), ("HIDDEN", "Toggle hidden files (Ctrl+H)", self.toggle_hidden),
                           ("UNDO", "Undo last trash/move (Ctrl+Z)", self.do_undo)):
            head.addWidget(button(t, "ghost", tip, cb))
        root.addLayout(head)
        split = QSplitter()
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        fav = Panel("Favorites")
        self.favs = QListWidget()
        for p in [Path.home()] + [Path.home() / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "JarvisProjects")] + [Path("/"), Path("/etc")]:
            self.favs.addItem(str(p))
        self.favs.itemActivated.connect(lambda it: self.pane().go(Path(it.text())))
        self.favs.itemClicked.connect(lambda it: self.pane().go(Path(it.text())))
        fav.lay.addWidget(self.favs)
        sl.addWidget(fav, 2)
        st = Panel("Storage")
        self.storage_lbl = QLabel()
        self.storage_bar = QProgressBar()
        self.storage_bar.setTextVisible(False)
        st.lay.addWidget(self.storage_lbl)
        st.lay.addWidget(self.storage_bar)
        sl.addWidget(st)
        split.addWidget(side)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.tabs.count() > 1 and self.tabs.removeTab(i))
        split.addWidget(self.tabs)
        self.info = Panel("Preview")
        self.prev_img = QLabel()
        self.prev_img.setAlignment(Qt.AlignCenter)
        self.prev_txt = QLabel()
        self.prev_txt.setWordWrap(True)
        self.prev_txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prev_txt.setAlignment(Qt.AlignTop)
        self.prev_txt.setStyleSheet(f"font-family:'{theme.FONT_MONO}';font-size:10px")
        self.info.lay.addWidget(self.prev_img)
        self.info.lay.addWidget(self.prev_txt, 1)
        split.addWidget(self.info)
        split.setSizes([220, 640, 300])
        root.addWidget(split, 1)
        self.status = QLabel()
        self.status.setObjectName("dim")
        root.addWidget(self.status)
        for key, cb in (("Ctrl+T", lambda: self.new_tab(Path.home())), ("Ctrl+H", self.toggle_hidden),
                        ("Ctrl+C", self.copy), ("Ctrl+X", self.cut), ("Ctrl+V", self.paste), ("Delete", self.delete),
                        ("F2", self.rename), ("Ctrl+Z", self.do_undo), ("Ctrl+Shift+N", self.new_folder),
                        ("Backspace", lambda: self.pane().up()), ("Alt+Left", lambda: self.pane().back()),
                        ("Ctrl+F", lambda: self.pane().search.setFocus())):
            QShortcut(QKeySequence(key), self, cb)
        self.new_tab(Path(start).expanduser() if start else Path.home())

    def pane(self) -> Pane:
        return self.tabs.currentWidget()

    def new_tab(self, p: Path):
        pane = Pane(self, Path.home())
        self.tabs.addTab(pane, "")
        self.tabs.setCurrentWidget(pane)
        pane.go(p)

    def update_storage(self, p: Path):
        try:
            du = shutil.disk_usage(p)
            self.storage_bar.setValue(int(100 * du.used / du.total))
            self.storage_lbl.setText(f"{du.free / 2**30:.1f} GB free of {du.total / 2**30:.1f} GB")
        except OSError:
            pass

    def preview(self, p: Path, full=False):
        if not p or not p.exists():
            return
        s = p.lstat()
        info = (f"{p.name}\n{'folder' if p.is_dir() else mimetypes.guess_type(p.name)[0] or 'file'}\n"
                f"size   {s.st_size:,} B\nmode   {stat.filemode(s.st_mode)}\nowner  {s.st_uid}:{s.st_gid}\n"
                f"mod    {time.strftime('%F %T', time.localtime(s.st_mtime))}\n")
        self.prev_img.clear()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") and s.st_size < 20_000_000:
            self.prev_img.setPixmap(QPixmap(str(p)).scaled(260, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif p.is_file() and s.st_size < 2_000_000 and _is_text(p):
            try:
                info += "\n" + p.read_text(errors="replace")[:1200]
            except OSError:
                info += "\n(permission denied)"
        elif p.is_dir():
            try:
                info += f"\n{len(list(p.iterdir()))} items"
            except OSError:
                pass
        self.prev_txt.setText(info)

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        for i in range(self.tabs.count()):
            m = self.tabs.widget(i).model
            f = QDir.AllEntries | QDir.NoDotAndDotDot | (QDir.Hidden if self.show_hidden else QDir.Filter(0))
            m.setFilter(f)

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Name:")
        if ok and name:
            try:
                (self.pane().cwd() / name).mkdir()
            except OSError as e:
                QMessageBox.warning(self, "Failed", str(e))

    def rename(self):
        sel = self.pane().selected()
        if not sel:
            return
        p = sel[0]
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=p.name)
        if ok and name and name != p.name:
            dst = p.with_name(name)
            if dst.exists():
                return QMessageBox.warning(self, "Exists", f"{name} already exists")
            p.rename(dst)
            self.undo.append({"move": (str(dst), str(p))})

    def copy(self):
        self.clip = ("copy", self.pane().selected())
        self.status.setText(f"{len(self.clip[1])} item(s) copied")

    def cut(self):
        self.clip = ("cut", self.pane().selected())
        self.status.setText(f"{len(self.clip[1])} item(s) cut")

    def paste(self):
        if not self.clip:
            return
        mode, items = self.clip
        dst_dir = self.pane().cwd()
        for p in items:
            dst = dst_dir / p.name
            n = 1
            while dst.exists():
                dst = dst_dir / f"{p.stem} ({n}){p.suffix}"
                n += 1
            try:
                if mode == "copy":
                    (shutil.copytree if p.is_dir() else shutil.copy2)(p, dst)
                else:
                    shutil.move(str(p), str(dst))
                    self.undo.append({"move": (str(dst), str(p))})
            except OSError as e:
                QMessageBox.warning(self, "Paste failed", f"{p}: {e}")
        if mode == "cut":
            self.clip = None

    def delete(self):
        sel = self.pane().selected()
        if not sel:
            return
        if QMessageBox.question(self, "Move to trash", f"Move {len(sel)} item(s) to trash?\n" +
                                "\n".join(str(p) for p in sel[:8])) != QMessageBox.Yes:
            return
        for p in sel:
            r = trash(str(p))
            if r.get("ok"):
                self.undo.append({"move": (r["undo"]["src"], r["undo"]["dst"])})
            else:
                QMessageBox.warning(self, "Delete failed", r.get("error", ""))
        self.status.setText(f"moved {len(sel)} item(s) to trash — Ctrl+Z to undo")

    def do_undo(self):
        if not self.undo:
            return
        src, dst = self.undo.pop()["move"]
        shutil.move(src, dst)
        self.status.setText(f"restored {dst}")

    def add_fav(self):
        for p in self.pane().selected():
            if p.is_dir():
                self.favs.addItem(str(p))


def build_for_screenshot():
    return FileManager(str(Path(__file__).resolve().parents[2]))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    app = theme.ensure_app(sys.argv)
    theme.apply(app)
    w = FileManager(argv[0] if argv else None)
    w.resize(1200, 720)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
