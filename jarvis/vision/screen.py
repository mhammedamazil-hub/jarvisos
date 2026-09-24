"""Strategic screenshots + screen analysis (local OCR and/or vision model)."""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def capture(scale: float = 0.5, region: tuple | None = None):
    """Return a PIL image (downscaled to save CPU / tokens). None if no display."""
    try:
        from PIL import Image
    except ImportError:
        return None
    img = None
    try:
        import mss
        with mss.mss() as s:
            mon = s.monitors[1] if not region else {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
            raw = s.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception:
        if shutil.which("scrot"):
            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                if subprocess.run(["scrot", "-o", f.name], capture_output=True).returncode == 0:
                    img = Image.open(f.name).convert("RGB")
                    img.load()
    if img is None:
        return None
    img.info["full_size"] = img.size
    if scale < 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        img.info["full_size"] = img.info.get("full_size")
    img.info["scale"] = scale
    return img


def to_data_url(img, quality: int = 70) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ocr(img) -> str:
    """Local OCR via tesseract (no cloud)."""
    if img is None or not shutil.which("tesseract"):
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        r = subprocess.run(["tesseract", f.name, "-", "--psm", "11"], capture_output=True, text=True, timeout=30)
        return r.stdout


def ocr_boxes(img) -> list[dict]:
    """Words with bounding boxes (in full-screen coordinates) for click targeting."""
    if img is None or not shutil.which("tesseract"):
        return []
    scale = img.info.get("scale", 1.0) or 1.0
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        r = subprocess.run(["tesseract", f.name, "-", "--psm", "11", "tsv"], capture_output=True, text=True, timeout=30)
    out = []
    for line in r.stdout.splitlines()[1:]:
        p = line.split("\t")
        if len(p) == 12 and p[11].strip() and float(p[10]) > 50:
            x, y, w, h = (int(v) / scale for v in p[6:10])
            out.append({"text": p[11], "x": int(x + w / 2), "y": int(y + h / 2), "conf": float(p[10])})
    return out


def find_text(img, text: str) -> dict | None:
    t = text.lower()
    for b in ocr_boxes(img):
        if t in b["text"].lower():
            return b
    return None


class ScreenObserver:
    """Avoids redundant captures: only re-captures when asked or after `min_interval`."""

    def __init__(self, scale: float = 0.5, min_interval: float = 1.0):
        self.scale, self.min_interval = scale, min_interval
        self._last = None
        self._t = 0.0

    def observe(self, force: bool = False):
        if force or self._last is None or time.time() - self._t > self.min_interval:
            self._last = capture(self.scale)
            self._t = time.time()
        return self._last
