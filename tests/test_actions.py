import threading

import pytest

from jarvis.computer_control.actions import validate_batch, ActionError, PyAutoGUIController, dry_run
from jarvis.core.safety import STOP, StopRequested

SIZE = (1366, 768)


def test_valid_batch():
    b = {"actions": [{"type": "open_app", "app": "blender"}, {"type": "wait", "seconds": 4},
                     {"type": "click", "x": 620, "y": 430}, {"type": "type", "text": "example"},
                     {"type": "press", "key": "enter"}]}
    assert len(validate_batch(b, SIZE)) == 5
    assert dry_run(b)[2] == "3. click at (620, 430)"


@pytest.mark.parametrize("bad", [
    {"type": "click", "x": 5000, "y": 10}, {"type": "click", "x": -1, "y": 10},
    {"type": "teleport"}, {"type": "type"}, {"type": "wait", "seconds": 999},
    {"type": "hotkey", "keys": ["ctrl", "alt", "delete"]}, {"type": "click", "x": "1", "y": 2},
    {"type": "click", "x": 1, "y": 2, "evil": True}, {"type": "click", "x": 1, "y": 2, "button": "nose"},
])
def test_invalid_actions(bad):
    with pytest.raises(ActionError):
        validate_batch([bad], SIZE)


def test_empty_and_huge():
    with pytest.raises(ActionError):
        validate_batch({"actions": []}, SIZE)
    with pytest.raises(ActionError):
        validate_batch([{"type": "press", "key": "a"}] * 101, SIZE)


def test_execute_with_fake_backend(fake_gui):
    launched = []
    c = PyAutoGUIController(backend=fake_gui, launcher=lambda a: launched.append(a) or {"ok": True})
    res = c.execute([{"type": "open_app", "app": "code"}, {"type": "click", "x": 10, "y": 20},
                     {"type": "type", "text": "hello"}, {"type": "hotkey", "keys": ["ctrl", "s"]}])
    assert all(r.ok for r in res) and launched == ["code"]
    names = [c[0] for c in fake_gui.calls]
    assert names == ["click", "write", "hotkey"]


def test_failure_stops_batch_and_retries(fake_gui):
    n = {"c": 0}
    def launcher(a):
        n["c"] += 1
        return {"ok": False, "error": "not installed"}
    c = PyAutoGUIController(backend=fake_gui, launcher=launcher)
    res = c.execute([{"type": "open_app", "app": "x"}, {"type": "click", "x": 1, "y": 1}], retries=2)
    assert len(res) == 1 and not res[0].ok and n["c"] == 3


def test_emergency_stop_interrupts(fake_gui):
    c = PyAutoGUIController(backend=fake_gui)
    threading.Timer(0.2, STOP.trigger, args=("ESC",)).start()
    with pytest.raises(StopRequested):
        c.execute([{"type": "wait", "seconds": 10}, {"type": "click", "x": 1, "y": 1}])
    assert not any(x[0] == "click" for x in fake_gui.calls)
