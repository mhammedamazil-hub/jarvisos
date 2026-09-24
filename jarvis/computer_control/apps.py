"""Discover installed applications from the freedesktop .desktop registry."""
from __future__ import annotations

import configparser
import difflib
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DesktopApp:
    id: str
    name: str
    exec: str
    icon: str = ""
    categories: str = ""
    comment: str = ""
    terminal: bool = False
    path: str = ""
    keywords: str = ""

    def argv(self) -> list[str]:
        # strip field codes (%f %U etc.)
        cmd = re.sub(r"%[fFuUdDnNickvm]", "", self.exec).strip()
        return shlex.split(cmd)


def data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")))
    dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    extra = ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share"),
             "/var/lib/snapd/desktop"]
    return [home] + [Path(d) for d in dirs + extra]


def parse_desktop_file(path: Path) -> DesktopApp | None:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if not cp.has_section("Desktop Entry"):
        return None
    e = cp["Desktop Entry"]
    if e.get("Type", "Application") != "Application" or e.get("NoDisplay", "false") == "true" \
            or e.get("Hidden", "false") == "true" or not e.get("Exec"):
        return None
    try_exec = e.get("TryExec")
    if try_exec and not (shutil.which(try_exec) or Path(try_exec).exists()):
        return None
    return DesktopApp(id=path.stem, name=e.get("Name", path.stem), exec=e["Exec"], icon=e.get("Icon", ""),
                      categories=e.get("Categories", ""), comment=e.get("Comment", ""),
                      terminal=e.get("Terminal", "false") == "true", path=str(path), keywords=e.get("Keywords", ""))


def discover(dirs: list[Path] | None = None) -> dict[str, DesktopApp]:
    apps: dict[str, DesktopApp] = {}
    for d in (dirs or data_dirs()):
        appdir = d / "applications" if d.name != "applications" else d
        if not appdir.is_dir():
            continue
        for f in sorted(appdir.rglob("*.desktop")):
            if f.stem in apps:  # earlier dirs take precedence (user overrides)
                continue
            a = parse_desktop_file(f)
            if a:
                apps[a.id] = a
    return apps


# Official install hints for apps we cannot ship (proprietary or large).
INSTALL_HINTS = {
    "code": "VS Code: download the .deb from https://code.visualstudio.com or `flatpak install flathub com.visualstudio.code`. VSCodium: `flatpak install flathub com.vscodium.codium`.",
    "brave": "Brave: follow https://brave.com/linux/ (adds the official brave-browser apt repo).",
    "discord": "Discord: official .deb from https://discord.com/download, `flatpak install flathub com.discordapp.Discord`, or use https://discord.com/app in a browser.",
    "blender": "Blender: `sudo apt install blender` or official tarball from https://www.blender.org/download/.",
    "unreal": "Unreal Engine for Linux requires an Epic account: https://www.unrealengine.com/linux. Needs ~100 GB disk and a dedicated GPU; not practical on 4 GB RAM.",
    "godot": "Godot: `flatpak install flathub org.godotengine.Godot` or https://godotengine.org/download/linux/.",
    "obs": "OBS Studio: `sudo apt install obs-studio`.",
    "gimp": "GIMP: `sudo apt install gimp`.",
    "krita": "Krita: `sudo apt install krita`.",
}


class AppRegistry:
    def __init__(self, aliases: dict[str, str] | None = None, dirs: list[Path] | None = None):
        self.aliases = {k.lower(): v for k, v in (aliases or {}).items()}
        self.dirs = dirs
        self.refresh()

    def refresh(self) -> None:
        self.apps = discover(self.dirs)

    def resolve(self, query: str) -> DesktopApp | None:
        q = query.strip().lower()
        q = self.aliases.get(q, q)
        if q in self.apps:
            return self.apps[q]
        for a in self.apps.values():
            names = [a.name.lower(), a.id.lower(), (a.argv() or [""])[0].split("/")[-1].lower()]
            if q in names or any(n.split(".")[-1] == q for n in names):
                return a
        for a in self.apps.values():
            if q in a.name.lower() or q in a.keywords.lower():
                return a
        names = {a.name.lower(): a for a in self.apps.values()}
        m = difflib.get_close_matches(q, names.keys(), n=1, cutoff=0.75)
        return names[m[0]] if m else None

    def launch(self, query: str, dry_run: bool = False) -> dict:
        a = self.resolve(query)
        if a:
            argv = a.argv()
            if a.terminal:
                argv = ["x-terminal-emulator", "-e"] + argv
        else:
            exe = shutil.which(self.aliases.get(query.lower(), query))
            if not exe:
                hint = next((v for k, v in INSTALL_HINTS.items() if k in query.lower()), None)
                return {"ok": False, "error": f"application '{query}' is not installed",
                        "install_hint": hint or f"try: apt search {query}"}
            argv = [exe]
        if dry_run:
            return {"ok": True, "argv": argv, "dry_run": True}
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "argv": argv, "pid": p.pid, "name": a.name if a else query}

    def installed(self, query: str) -> bool:
        return self.resolve(query) is not None or shutil.which(query) is not None
