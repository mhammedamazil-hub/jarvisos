"""Emergency stop, risk classification, permissions and approval policy.

Every action — whether it came from text, voice, a gesture, a clap, an MCP call
or the planner — passes through `PolicyEngine.decide()` before execution.
"""
from __future__ import annotations

import json
import re
import shlex
import threading
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable

from .config import jarvis_home


class StopRequested(Exception):
    """Raised inside running automation when the user hits STOP / ESC."""


class EmergencyStop:
    """Process-wide kill switch. Automation must call `check()` between steps."""

    def __init__(self):
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self.reason = ""

    def trigger(self, reason: str = "user") -> None:
        self.reason = reason
        self._event.set()
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass

    def reset(self) -> None:
        self._event.clear()
        self.reason = ""

    @property
    def active(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise StopRequested(self.reason or "stopped")

    def wait(self, seconds: float) -> None:
        """Interruptible sleep."""
        if self._event.wait(seconds):
            raise StopRequested(self.reason or "stopped")

    def on_trigger(self, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)


STOP = EmergencyStop()


class Risk(int, Enum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class OpType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    SEND = "SEND"
    EXECUTE = "EXECUTE"
    SYSTEM_CHANGE = "SYSTEM_CHANGE"


PERMISSIONS = ("root", "file_modify", "file_delete", "external_comm", "send_messages", "cloud", "mcp",
               "browser", "package_install", "system_change", "camera", "microphone", "input_control",
               "run_commands")


@dataclass
class Assessment:
    risk: Risk
    op: OpType
    permissions: list[str]
    reasons: list[str] = field(default_factory=list)
    reversible: bool = True
    critical: bool = False   # always confirm, even in FULL mode

    def to_dict(self):
        d = asdict(self)
        d["risk"] = self.risk.name
        d["op"] = self.op.value
        return d


# --- shell command risk analysis -------------------------------------------------
CRITICAL_PATTERNS = [
    (r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    (r"\bdd\b.*\bof=/dev/", "writes raw data to a block device"),
    (r"\b(fdisk|sfdisk|gdisk|sgdisk|parted|cfdisk|wipefs)\b", "modifies partition tables"),
    (r"\befibootmgr\b", "modifies EFI boot entries"),
    (r"\bgrub-install\b|\bupdate-grub\b", "modifies the bootloader"),
    (r"\brm\s+(-[a-zA-Z]*\s+)*(/|/\*|~|\$HOME|/home|/etc|/usr|/boot|/var)\s*($|;|&)", "recursive delete of a critical path"),
    (r"--no-preserve-root", "disables root protection"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(passwd|chpasswd|usermod|userdel|visudo)\b|/etc/(shadow|sudoers|pam\.d)", "changes authentication"),
    (r"\b(setenforce\s+0|ufw\s+disable|aa-teardown|systemctl\s+(stop|disable)\s+(apparmor|ufw|firewalld))", "disables security protection"),
    (r">\s*/dev/sd[a-z]|>\s*/dev/nvme", "overwrites a disk"),
    (r"/boot/efi|ntfs|/mnt/c\b|Windows", "touches EFI/Windows data"),
]
HIGH_PATTERNS = [
    (r"^\s*sudo\b|\bpkexec\b|\bsu\s+-c\b", "requires root"),
    (r"\brm\b.*-[a-zA-Z]*r", "recursive delete"),
    (r"\bchmod\s+-R\b|\bchown\s+-R\b", "recursive permission change"),
    (r"\b(apt|apt-get|dpkg|pip|pip3|npm|snap|flatpak)\s+(install|remove|purge|uninstall|-i)\b", "installs/removes software"),
    (r"\bsystemctl\s+(enable|disable|mask|stop|restart)\b", "changes services"),
    (r"\bcurl\b.*\|\s*(sh|bash)|\bwget\b.*\|\s*(sh|bash)", "executes remote script"),
    (r"\bgit\s+push\b|\bgit\s+branch\s+-D\b|\bgit\s+reset\s+--hard\b", "irreversible/remote git operation"),
    (r"\b(shutdown|reboot|poweroff|halt)\b", "power state change"),
]
MEDIUM_PATTERNS = [
    (r"\brm\b|\bmv\b|\bshred\b|\btruncate\b", "deletes or moves files"),
    (r"(^|[^2])>\s*[^&]", "overwrites a file"),
    (r"\bkill(all)?\b|\bpkill\b", "terminates processes"),
    (r"\bcurl\b|\bwget\b|\bssh\b|\bscp\b|\brsync\b", "network access"),
    (r"\bgit\s+commit\b|\bgit\s+checkout\b", "modifies repository"),
]
READ_ONLY_CMDS = {"ls", "cat", "pwd", "echo", "whoami", "date", "uname", "df", "du", "free", "head", "tail",
                  "grep", "find", "which", "ps", "top", "uptime", "lsblk", "ip", "id", "env", "wc", "file",
                  "stat", "journalctl", "git", "python3", "hostname", "lscpu", "nproc", "sensors"}


def assess_command(cmd: str) -> Assessment:
    reasons, perms = [], ["run_commands"]
    for pat, why in CRITICAL_PATTERNS:
        if re.search(pat, cmd):
            reasons.append(why)
    if reasons:
        if "requires root" not in reasons and re.search(r"^\s*sudo\b", cmd):
            perms.append("root")
        return Assessment(Risk.CRITICAL, OpType.SYSTEM_CHANGE, perms + ["system_change"], reasons, False, True)
    for pat, why in HIGH_PATTERNS:
        if re.search(pat, cmd):
            reasons.append(why)
    if reasons:
        if any("root" in r for r in reasons):
            perms.append("root")
        if any("software" in r for r in reasons):
            perms.append("package_install")
        op = OpType.DELETE if any("delete" in r for r in reasons) else OpType.SYSTEM_CHANGE
        irreversible = any(k in r for r in reasons for k in ("delete", "remote", "irreversible", "power", "recursive"))
        return Assessment(Risk.HIGH, op, perms, reasons, reversible=not irreversible)
    for pat, why in MEDIUM_PATTERNS:
        if re.search(pat, cmd):
            reasons.append(why)
    if reasons:
        op = OpType.DELETE if "deletes or moves files" in reasons else OpType.EXECUTE
        return Assessment(Risk.MEDIUM, op, perms, reasons, reversible=op != OpType.DELETE)
    try:
        first = shlex.split(cmd)[0] if cmd.strip() else ""
    except ValueError:
        first = cmd.split()[0] if cmd.split() else ""
    if Path(first).name in READ_ONLY_CMDS and not re.search(r"[;&|`$]", cmd):
        return Assessment(Risk.LOW, OpType.READ, perms, ["read-only command"])
    return Assessment(Risk.MEDIUM, OpType.EXECUTE, perms, ["arbitrary command"])


SYSTEM_PATHS = ("/etc", "/usr", "/boot", "/var", "/opt", "/lib", "/bin", "/sbin", "/root", "/sys", "/proc", "/dev")
FORBIDDEN_PATHS = ("/boot/efi", "/dev/sd", "/dev/nvme", "/dev/mmcblk", "/etc/shadow", "/etc/sudoers")


def assess_path(path: str, op: OpType) -> Assessment:
    p = str(Path(path).expanduser().resolve()) if path else ""
    perms = {OpType.READ: [], OpType.WRITE: ["file_modify"], OpType.DELETE: ["file_delete"]}.get(op, ["file_modify"])
    if any(p.startswith(f) for f in FORBIDDEN_PATHS):
        return Assessment(Risk.CRITICAL, op, perms + ["root", "system_change"], [f"{p} is boot/disk/auth critical"], False, True)
    if any(p == s or p.startswith(s + "/") for s in SYSTEM_PATHS):
        if op == OpType.READ:
            return Assessment(Risk.LOW, op, perms, ["system file read"])
        return Assessment(Risk.HIGH, op, perms + ["root"], [f"modifies system path {p}"], reversible=op != OpType.DELETE)
    if op == OpType.READ:
        return Assessment(Risk.NONE, op, perms)
    if op == OpType.DELETE:
        return Assessment(Risk.MEDIUM, op, perms, ["deletes user file"], reversible=True)  # trash-based
    return Assessment(Risk.LOW, op, perms, ["modifies user file"])


# --- mode policy -------------------------------------------------------------------
class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionStore:
    """Persisted grants. A permission must be granted before actions needing it may run
    without asking. Grants are revocable and audited."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else jarvis_home() / "permissions.json"
        self.grants: dict[str, bool] = {}
        if self.path.exists():
            try:
                self.grants = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self.grants = {}

    def granted(self, perm: str) -> bool:
        return bool(self.grants.get(perm, perm in ("input_control", "run_commands", "file_modify")))

    def set(self, perm: str, value: bool) -> None:
        if perm not in PERMISSIONS:
            raise ValueError(f"unknown permission {perm}")
        self.grants[perm] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.grants, indent=2))
        self.path.chmod(0o600)

    def list(self) -> dict[str, bool]:
        return {p: self.granted(p) for p in PERMISSIONS}


AUTO_ALLOW_MAX = {"safe": Risk.NONE, "assisted": Risk.LOW, "autonomous": Risk.MEDIUM, "full": Risk.HIGH}
ALWAYS_ASK_OPS = {"assisted": {OpType.SEND, OpType.DELETE, OpType.SYSTEM_CHANGE},
                  "autonomous": {OpType.SEND, OpType.SYSTEM_CHANGE},
                  "full": set(), "safe": set(OpType)}


class PolicyEngine:
    def __init__(self, mode_getter: Callable[[], str], permissions: PermissionStore):
        self.mode_getter = mode_getter
        self.permissions = permissions

    def decide(self, a: Assessment) -> tuple[Decision, str]:
        mode = self.mode_getter()
        if a.critical or a.risk >= Risk.CRITICAL:
            return Decision.ASK, "critical operation always requires explicit confirmation"
        if mode == "safe" and a.risk > Risk.NONE:
            return Decision.ASK, "SAFE mode: approval required"
        if a.risk > AUTO_ALLOW_MAX[mode]:
            return Decision.ASK, f"risk {a.risk.name} exceeds {mode.upper()} auto-approve limit"
        if a.op in ALWAYS_ASK_OPS.get(mode, set()) and a.risk > Risk.NONE:
            return Decision.ASK, f"{a.op.value} operations need approval in {mode.upper()} mode"
        if not a.reversible and a.risk >= (Risk.HIGH if mode == "full" else Risk.MEDIUM):
            return Decision.ASK, "irreversible operation requires confirmation"
        missing = [p for p in a.permissions if not self.permissions.granted(p)]
        if missing:
            return Decision.ASK, f"permission not granted: {', '.join(missing)}"
        return Decision.ALLOW, "within policy"


@dataclass
class ApprovalRequest:
    id: str
    title: str
    reason: str
    assessment: dict
    details: str
    backup_available: bool = False
    status: str = "pending"   # pending/approved/denied/edited
    edited: dict | None = None


class ApprovalBroker:
    """Hands approval requests to whichever UI is attached (HUD, CLI, test)."""

    def __init__(self):
        self.handler: Callable[[ApprovalRequest], str] | None = None
        self.pending: dict[str, ApprovalRequest] = {}
        self._events: dict[str, threading.Event] = {}
        self._n = 0
        self._lock = threading.Lock()
        self.always_allow: set[str] = set()   # "approve all safe steps" for current task

    def request(self, title: str, reason: str, assessment: Assessment, details: str,
                backup_available: bool = False, timeout: float = 300) -> ApprovalRequest:
        with self._lock:
            self._n += 1
            req = ApprovalRequest(f"ap{self._n}", title, reason, assessment.to_dict(), details, backup_available)
        if self.handler:  # synchronous handler (CLI / tests)
            req.status = self.handler(req)
            return req
        ev = threading.Event()
        self.pending[req.id] = req
        self._events[req.id] = ev
        end = timeout
        while end > 0:
            if ev.wait(0.2):
                break
            if STOP.active:
                req.status = "denied"
                break
            end -= 0.2
        else:
            req.status = "denied"
        self.pending.pop(req.id, None)
        self._events.pop(req.id, None)
        return req

    def respond(self, req_id: str, status: str) -> bool:
        req = self.pending.get(req_id)
        if not req:
            return False
        req.status = status
        self._events[req_id].set()
        return True
