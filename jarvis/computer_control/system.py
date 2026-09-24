"""Terminal execution, window management, file operations and telemetry."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..core.safety import STOP, StopRequested


# ------------------------------------------------------------------ terminal
def run_command(cmd: str, timeout: float = 60, cwd: str | None = None, sudo: bool = False,
                env: dict | None = None) -> dict:
    """Run a shell command visibly and interruptibly. Never hides the command."""
    if sudo and not cmd.lstrip().startswith(("sudo", "pkexec")):
        # pkexec gives a graphical polkit password prompt; sudo -A uses SUDO_ASKPASS.
        cmd = ("pkexec sh -c " if shutil.which("pkexec") and os.environ.get("DISPLAY") else "sudo ") + \
              (repr(cmd) if shutil.which("pkexec") and os.environ.get("DISPLAY") else cmd)
    start = time.time()
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True, env={**os.environ, **(env or {})})
    try:
        while True:
            try:
                out, err = p.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if STOP.active or time.time() - start > timeout:
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        out, err = p.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        out, err = p.communicate()
                    if STOP.active:
                        raise StopRequested("command interrupted")
                    return {"ok": False, "cmd": cmd, "code": None, "stdout": out[-20000:], "stderr": err[-20000:],
                            "error": f"timeout after {timeout}s", "duration": time.time() - start}
    except StopRequested:
        raise
    return {"ok": p.returncode == 0, "cmd": cmd, "code": p.returncode, "stdout": out[-20000:],
            "stderr": err[-20000:], "duration": round(time.time() - start, 3)}


ERROR_HINTS = [
    ("command not found", "The program is not installed. Try `apt search <name>` then `sudo apt install <pkg>`."),
    ("Permission denied", "Needs different permissions. Check ownership (`ls -l`) or rerun with root approval."),
    ("No such file or directory", "Path does not exist. Check spelling or create it first."),
    ("ModuleNotFoundError", "Python module missing. `pip install --user <module>` or `sudo apt install python3-<module>`."),
    ("Could not resolve host", "No network/DNS. Check `nmcli dev status`."),
    ("E: Unable to locate package", "Package unknown: run `sudo apt update` or check the package name."),
    ("Address already in use", "Port busy. Find it with `ss -ltnp`."),
    ("npm ERR!", "npm failed — try `npm ci` or delete node_modules and reinstall."),
    ("fatal: not a git repository", "Not inside a git repo — cd into the project or run `git init`."),
]


def diagnose(result: dict) -> list[str]:
    text = (result.get("stderr", "") + result.get("stdout", "") + str(result.get("error", "")))
    return [hint for pat, hint in ERROR_HINTS if pat in text]


# ------------------------------------------------------------------ windows
def _have(x: str) -> bool:
    return shutil.which(x) is not None


def list_windows() -> list[dict]:
    if not _have("wmctrl"):
        return []
    r = subprocess.run(["wmctrl", "-lpG"], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) >= 8:
            out.append({"id": parts[0], "desktop": int(parts[1]), "pid": int(parts[2]), "x": int(parts[3]),
                        "y": int(parts[4]), "w": int(parts[5]), "h": int(parts[6]),
                        "title": parts[8] if len(parts) > 8 else ""})
    return out


def active_window() -> dict:
    if _have("xdotool"):
        r = subprocess.run(["xdotool", "getactivewindow", "getwindowname"], capture_output=True, text=True)
        if r.returncode == 0:
            return {"title": r.stdout.strip()}
    return {"title": ""}


def window_command(command: str, target: str | None = None, **geo) -> dict:
    """Workspace / window operations via wmctrl + xdotool (EWMH, works on Openbox)."""
    if not _have("wmctrl"):
        return {"ok": False, "error": "wmctrl not installed"}
    def cur_desktop():
        r = subprocess.run(["wmctrl", "-d"], capture_output=True, text=True)
        lines = r.stdout.splitlines()
        cur = next((i for i, l in enumerate(lines) if " * " in l), 0)
        return cur, max(len(lines), 1)
    if command in ("next_workspace", "prev_workspace"):
        cur, n = cur_desktop()
        nxt = (cur + (1 if command == "next_workspace" else -1)) % n
        argv = ["wmctrl", "-s", str(nxt)]
    elif command == "focus" and target:
        argv = ["wmctrl", "-a", target]
    elif command == "close" and target:
        argv = ["wmctrl", "-c", target]
    elif command == "move_resize" and target:
        argv = ["wmctrl", "-r", target, "-e", f"0,{geo.get('x',-1)},{geo.get('y',-1)},{geo.get('w',-1)},{geo.get('h',-1)}"]
    elif command == "maximize" and target:
        argv = ["wmctrl", "-r", target, "-b", "add,maximized_vert,maximized_horz"]
    elif command == "minimize" and _have("xdotool"):
        argv = ["xdotool", "getactivewindow", "windowminimize"]
    elif command == "show_desktop":
        argv = ["wmctrl", "-k", "on"]
    else:
        return {"ok": False, "error": f"unsupported window command {command}"}
    r = subprocess.run(argv, capture_output=True, text=True)
    return {"ok": r.returncode == 0, "argv": argv, "stderr": r.stderr}


def wait_for_window(title_substr: str, timeout: float = 15) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        STOP.check()
        for w in list_windows():
            if title_substr.lower() in w["title"].lower():
                return w
        STOP.wait(0.5)
    return None


# ------------------------------------------------------------------ files
def trash_dir() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))) / "Trash"
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "info").mkdir(parents=True, exist_ok=True)
    return d


def trash(path: str) -> dict:
    """Freedesktop-compatible trash so deletes are undoable."""
    src = Path(path).expanduser()
    if not src.exists() and not src.is_symlink():
        return {"ok": False, "error": f"{src} does not exist"}
    t = trash_dir()
    name = src.name
    dst = t / "files" / name
    i = 1
    while dst.exists():
        dst = t / "files" / f"{src.stem}.{i}{src.suffix}"
        i += 1
    (t / "info" / f"{dst.name}.trashinfo").write_text(
        f"[Trash Info]\nPath={src.resolve()}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(str(src), str(dst))
    return {"ok": True, "trashed": str(dst), "undo": {"type": "move_file", "src": str(dst), "dst": str(src.resolve())}}


def backup_file(path: str) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    from ..core.config import jarvis_home
    bdir = jarvis_home() / "file-backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{str(p.resolve()).strip('/').replace('/', '__')}.{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(p, dst)
    except PermissionError:
        r = subprocess.run(["sudo", "-n", "cp", "-a", str(p), str(dst)], capture_output=True)
        if r.returncode != 0:
            return None
    return str(dst)


def write_file(path: str, content: str, root: bool = False) -> dict:
    p = Path(path).expanduser()
    bak = backup_file(str(p))
    try:
        if root:
            r = subprocess.run(["sudo", "tee", str(p)], input=content, text=True, capture_output=True)
            if r.returncode:
                return {"ok": False, "error": r.stderr}
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    except OSError as e:
        return {"ok": False, "error": str(e), "needs_root": isinstance(e, PermissionError)}
    return {"ok": True, "path": str(p), "backup": bak}


# ------------------------------------------------------------------ telemetry
def telemetry() -> dict:
    """System stats; psutil if available, /proc fallback."""
    d: dict = {"time": time.time()}
    try:
        import psutil
        d["cpu"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        d["ram_pct"], d["ram_avail_mb"], d["ram_total_mb"] = vm.percent, vm.available // 2**20, vm.total // 2**20
        du = psutil.disk_usage("/")
        d["disk_pct"], d["disk_free_gb"] = du.percent, round(du.free / 2**30, 1)
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b:
            d["battery"], d["charging"] = round(b.percent), b.power_plugged
        try:
            temps = psutil.sensors_temperatures()
            vals = [t.current for arr in temps.values() for t in arr if t.current]
            if vals:
                d["temp_c"] = round(max(vals))
        except (AttributeError, OSError):
            pass
        net = psutil.net_if_stats()
        d["net_up"] = any(s.isup for n, s in net.items() if n != "lo")
        io = psutil.net_io_counters()
        d["net_rx"], d["net_tx"] = io.bytes_recv, io.bytes_sent
    except ImportError:
        try:
            mem = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
            tot = int(mem["MemTotal"].split()[0]) // 1024
            av = int(mem["MemAvailable"].split()[0]) // 1024
            d.update(ram_total_mb=tot, ram_avail_mb=av, ram_pct=round(100 * (tot - av) / tot, 1))
            d["cpu"] = round(os.getloadavg()[0] * 100 / (os.cpu_count() or 1), 1)
            st = os.statvfs("/")
            d["disk_pct"] = round(100 * (1 - st.f_bavail / st.f_blocks), 1)
        except (OSError, KeyError):
            pass
    return d
