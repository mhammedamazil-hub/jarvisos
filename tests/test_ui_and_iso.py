import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- ISO tree (static)
def test_build_scripts_exist_and_parse():
    for s in ("build-iso.sh", "clean-build.sh", "run-qemu.sh", "test-iso.sh", "install-dependencies.sh"):
        p = ROOT / s
        assert p.exists() and os.access(p, os.X_OK), s
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0, s


def test_iso_config_sane():
    cfg = ROOT / "iso" / "config"
    pkgs = set()
    for f in (cfg / "package-lists").glob("*.list.chroot"):
        pkgs |= {l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")}
    for must in ("linux-image-amd64", "live-boot", "openbox", "lightdm", "network-manager", "pipewire",
                 "python3-pyqt5", "wmctrl", "xdotool", "lxterminal", "firefox-esr"):
        assert must in pkgs
    for heavy in ("gnome-shell", "kde-plasma-desktop", "ollama"):
        assert heavy not in pkgs
    for h in (cfg / "hooks" / "live").glob("*.hook.chroot"):
        assert os.access(h, os.X_OK)
    inc = cfg / "includes.chroot"
    assert "layer>below" in (inc / "etc/skel/.config/openbox/rc.xml").read_text()
    assert "MemoryMax" in (inc / "etc/systemd/user/jarvisd.service").read_text()
    auto = (ROOT / "iso/auto/config").read_text()
    assert "iso-hybrid" in auto and "grub-efi" in auto


def test_no_host_disk_writes_in_scripts():
    for s in ("build-iso.sh", "run-qemu.sh", "test-iso.sh", "clean-build.sh"):
        t = (ROOT / s).read_text()
        assert not re.search(r"\bdd\b|of=/dev/|mkfs|grub-install|efibootmgr|parted", t), s


# ---------------------------------------------------------------- UI (offscreen)
qt = pytest.importorskip("PyQt5.QtWidgets", reason="PyQt5 not available", exc_type=ImportError)


@pytest.fixture
def qapp(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from jarvis.desktop import theme
    return theme.ensure_app(["t"])


def test_hud_starts_offline_and_online(qapp, runtime):
    from jarvis.desktop.hud import HUD
    h = HUD()                       # core offline → fallback, must not crash
    assert not h.core.online
    h.poll_core()
    h.close()
    srv = runtime.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    h = HUD()
    h.poll_core()
    assert h.core.online
    h.cmd.setText("enable cinematic mode")
    h.send_command()
    import time
    for _ in range(40):
        if runtime.cfg.get("ui.mode") == "cinematic":
            break
        time.sleep(0.05)
    assert runtime.cfg.get("ui.mode") == "cinematic"
    h.refresh_config()
    assert h.cfg["ui.mode"] == "cinematic"
    h.tick()
    img = h.grab()
    assert not img.isNull()
    h.close()
    srv.shutdown()
    runtime.shutdown()


def test_settings_files_editor_build(qapp, tmp_path):
    from jarvis.desktop.settings import SettingsWindow
    from jarvis.desktop.files import FileManager
    from jarvis.desktop.editor import Editor
    s = SettingsWindow()
    assert s.tabs.count() >= 10
    s._set("ui.fps", 17)
    from jarvis.core.config import ConfigStore
    assert ConfigStore().get("ui.fps") == 17
    fm = FileManager(str(tmp_path))
    assert fm.pane().cwd() == tmp_path
    f = tmp_path / "a.txt"
    f.write_text("hello world")
    e = Editor(str(f))
    e.ed.setPlainText("hello jarvis")
    e.save()
    assert f.read_text() == "hello jarvis"
    assert not e.ed.document().isModified()
