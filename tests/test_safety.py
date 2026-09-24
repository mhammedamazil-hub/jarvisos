import json
import threading
import time

import pytest

from jarvis.core.safety import (assess_command, assess_path, Risk, OpType, PolicyEngine, PermissionStore, Decision,
                                STOP, StopRequested, ApprovalBroker)
from jarvis.core.audit import AuditLog, redact


@pytest.mark.parametrize("cmd,risk", [
    ("ls -la", Risk.LOW), ("cat /etc/hostname", Risk.LOW),
    ("rm notes.txt", Risk.MEDIUM), ("curl https://x.y", Risk.MEDIUM),
    ("sudo apt install vim", Risk.HIGH), ("git push origin main", Risk.HIGH), ("rm -rf build", Risk.HIGH),
    ("sudo mkfs.ext4 /dev/sda1", Risk.CRITICAL), ("dd if=x.iso of=/dev/sdb", Risk.CRITICAL),
    ("efibootmgr -b 0001 -B", Risk.CRITICAL), ("rm -rf /", Risk.CRITICAL), ("sudo parted /dev/sda rm 3", Risk.CRITICAL),
    ("echo x | sudo tee /etc/sudoers", Risk.CRITICAL), ("sudo systemctl disable apparmor", Risk.CRITICAL),
])
def test_command_risk(cmd, risk):
    assert assess_command(cmd).risk == risk


def test_path_risk():
    assert assess_path("/etc/hosts", OpType.WRITE).risk == Risk.HIGH
    assert "root" in assess_path("/etc/hosts", OpType.WRITE).permissions
    assert assess_path("/boot/efi/EFI/x", OpType.WRITE).critical
    assert assess_path("~/notes.txt", OpType.WRITE).risk == Risk.LOW
    assert assess_path("/etc/hosts", OpType.READ).risk == Risk.LOW


def test_policy_modes(tmp_path):
    mode = {"m": "safe"}
    pe = PolicyEngine(lambda: mode["m"], PermissionStore(tmp_path / "p.json"))
    low, med, high = assess_command("ls"), assess_command("rm a"), assess_command("sudo apt install x")
    crit = assess_command("mkfs /dev/sda")
    assert pe.decide(low)[0] == Decision.ASK
    mode["m"] = "assisted"
    assert pe.decide(low)[0] == Decision.ALLOW and pe.decide(med)[0] == Decision.ASK
    mode["m"] = "autonomous"
    assert pe.decide(med)[0] == Decision.ASK  # delete op still asks
    assert pe.decide(assess_command("kill 123"))[0] == Decision.ALLOW
    mode["m"] = "full"
    assert pe.decide(crit)[0] == Decision.ASK  # critical ALWAYS asks
    assert pe.decide(high)[0] == Decision.ASK  # root/package_install not granted
    ps = PermissionStore(tmp_path / "p.json")
    ps.set("root", True)
    ps.set("package_install", True)
    pe2 = PolicyEngine(lambda: "full", ps)
    assert pe2.decide(high)[0] == Decision.ALLOW
    assert pe2.decide(crit)[0] == Decision.ASK


def test_emergency_stop():
    STOP.trigger("test")
    with pytest.raises(StopRequested):
        STOP.check()
    STOP.reset()
    STOP.check()
    threading.Timer(0.1, STOP.trigger).start()
    t = time.time()
    with pytest.raises(StopRequested):
        STOP.wait(5)
    assert time.time() - t < 1


def test_approval_broker_async():
    b = ApprovalBroker()
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("r", b.request("t", "r", assess_command("rm x"), "d")))
    th.start()
    for _ in range(50):
        if b.pending:
            break
        time.sleep(0.02)
    rid = next(iter(b.pending))
    assert b.respond(rid, "approved")
    th.join(2)
    assert res["r"].status == "approved"


def test_audit_redaction(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.log("command", "curl -H 'Authorization: Bearer abc.def.ghi' x", api_key="sk-supersecret1234567890",
            args={"token": "t0k3n", "path": "/tmp/x"})
    raw = (tmp_path / "a.jsonl").read_text()
    assert "sk-supersecret" not in raw and "abc.def.ghi" not in raw and "t0k3n" not in raw
    assert "/tmp/x" in raw
    assert redact("key AIzaSyA1234567890123456789012345") == "key ***REDACTED***"
    log2 = AuditLog(tmp_path / "b.jsonl", disabled_categories={"gesture"})
    assert log2.log("gesture", "x") is None
    assert len(log.entries(query="curl")) == 1
