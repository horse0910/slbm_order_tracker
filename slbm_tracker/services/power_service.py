"""An explicit, reversible idle-sleep assertion owned by this app only."""
from __future__ import annotations
import os
import subprocess
import sys

class PowerService:
    def __init__(self):
        self._process = None

    def status(self):
        return {"supported": sys.platform == "darwin", "enabled": bool(self._process and self._process.poll() is None)}

    def set_enabled(self, enabled: bool):
        if enabled and sys.platform != "darwin":
            raise ValueError("Keeping the computer awake is available on macOS only.")
        if enabled and not self.status()["enabled"]:
            self._process = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not enabled and self._process:
            self._process.terminate()
            try: self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill(); self._process.wait(timeout=3)
            self._process = None
        return self.status()
