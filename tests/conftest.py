import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def jhome(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jhome"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_SCREEN", "1366x768")
    from jarvis.core.safety import STOP
    STOP.reset()
    yield tmp_path
    STOP.reset()


class FakeGUI:
    KEYBOARD_KEYS = ["enter", "esc", "a", "tab"]

    def __init__(self):
        self.calls = []

    def size(self):
        return (1366, 768)

    def __getattr__(self, name):
        def f(*a, **kw):
            self.calls.append((name, a, kw))
        return f


@pytest.fixture
def fake_gui():
    return FakeGUI()


@pytest.fixture
def runtime(tmp_path, fake_gui):
    from jarvis.core.runtime import JarvisRuntime
    rt = JarvisRuntime(gui_backend=fake_gui)
    rt.approvals.handler = lambda req: rt._answers.pop(0) if rt._answers else "denied"
    rt._answers = []
    rt._fake_gui = fake_gui
    return rt
