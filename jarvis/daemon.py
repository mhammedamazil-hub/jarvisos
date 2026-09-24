"""jarvisd — the JARVIS core daemon (runs as the logged-in user, never as root)."""
from __future__ import annotations

import logging
import signal
import sys
import threading

from .core.runtime import JarvisRuntime, socket_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s jarvisd %(message)s")
    rt = JarvisRuntime()
    rt.start()
    srv = rt.serve()
    logging.info("listening on %s (control mode: %s)", socket_path(), rt.cfg.get("control.mode"))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    stop.wait()
    srv.shutdown()
    rt.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
