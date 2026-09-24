import json
import os
import stat

import pytest

from jarvis.core.config import ConfigStore, ConfigError, apply_mode, validate_gesture_mapping


def test_defaults_and_validation(tmp_path):
    c = ConfigStore(tmp_path / "c.json")
    assert c.get("ui.mode") == "performance"
    with pytest.raises(ConfigError):
        c.set("vision.fps", 500)
    with pytest.raises(ConfigError):
        c.set("ui.mode", "psychedelic")
    with pytest.raises(ConfigError):
        c.set("nope.key", 1)
    assert c.set("vision.fps", "10") == 10
    assert c.set("audio.voice", "on") is True


def test_invalid_file_falls_back(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"settings": {"vision.fps": 9999, "ui.brightness": 1.5},
                             "gestures": [{"gesture": "BOGUS", "action": {"type": "UI"}}]}))
    c = ConfigStore(p)
    assert c.get("vision.fps") == 10  # default
    assert c.get("ui.brightness") == 1.5
    assert c.get("gestures") == []
    assert any("vision.fps" in w for w in c.load_warnings)
    p.write_text("{{corrupt")
    c = ConfigStore(p)
    assert c.get("ui.mode") == "performance"


def test_persist_backup_history_permissions(tmp_path):
    p = tmp_path / "c.json"
    c = ConfigStore(p)
    c.set("ui.brightness", 1.2)
    c.set("ui.brightness", 1.4)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert ConfigStore(p).get("ui.brightness") == 1.4
    assert len(c.backups()) >= 1
    assert c.history()[-1]["new"] == 1.4
    c.restore_backup()
    assert c.get("ui.brightness") == 1.2


def test_export_import_reset(tmp_path):
    c = ConfigStore(tmp_path / "c.json")
    c.set("ui.fps", 25)
    c.export(tmp_path / "e.json")
    c.reset()
    assert c.get("ui.fps") == 20
    c.import_(tmp_path / "e.json")
    assert c.get("ui.fps") == 25


def test_modes(tmp_path):
    c = ConfigStore(tmp_path / "c.json")
    apply_mode(c, "cinematic")
    assert c.get("ui.particles") and c.get("ui.orbital_rings")
    apply_mode(c, "performance")
    assert not c.get("ui.particles") and c.get("ui.transparency") == 0


def test_hardware_profile(tmp_path):
    c = ConfigStore(tmp_path / "c.json")
    c.set("ui.mode", "cinematic")
    changed = c.apply_hardware_profile(3900)
    assert "ui.mode" in changed and c.get("ui.mode") == "performance"


def test_gesture_mapping_validation(tmp_path):
    m = validate_gesture_mapping({"gesture": "two_fingers", "action": {"type": "open_application", "application": "code"}})
    assert m["gesture"] == "TWO_FINGERS" and m["action"]["type"] == "OPEN_APPLICATION"
    with pytest.raises(ConfigError):
        validate_gesture_mapping({"gesture": "TWO_FINGERS", "action": {"type": "RM_RF"}})
    c = ConfigStore(tmp_path / "c.json")
    c.upsert_gesture({"gesture": "PINCH", "action": {"type": "MOUSE", "button": "left"}})
    assert sum(g["gesture"] == "PINCH" for g in c.get("gestures")) == 1
