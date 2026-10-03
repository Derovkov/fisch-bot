"""
Per-run temporary storage that is deleted when the run ends.

Every file a run writes -- trace logs, per-reel summaries, debug images -- goes
into one folder under the system temp directory, named fischbot_<random>. It is
removed when the run stops (normally, via the UI's Stop, or on a crash through
atexit). Folders left behind by a run that was killed outright (power loss, task
manager) are swept the next time a session starts.

keep_logs=True copies the folder's contents to saved_logs/<timestamp>/ before
deleting it -- for when a log is wanted for troubleshooting.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

PREFIX = "fischbot_"
PID_FILE = "owner.pid"
STALE_AGE_S = 6 * 3600          # sweep an orphan folder this old even if unsure


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID is still running (Windows)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def sweep_stale() -> int:
    """Delete fischbot_* temp folders whose owning process is gone. Returns count."""
    removed = 0
    for d in Path(tempfile.gettempdir()).glob(PREFIX + "*"):
        if not d.is_dir():
            continue
        try:
            pid = int((d / PID_FILE).read_text().strip())
            alive = pid != os.getpid() and _pid_alive(pid)
        except (OSError, ValueError):
            alive = time.time() - d.stat().st_mtime < STALE_AGE_S
        if not alive:
            shutil.rmtree(d, ignore_errors=True)
            removed += not d.exists()
    return removed


class Session:
    """One run's temporary folder. Use .path(name) for every file the run writes."""

    def __init__(self, keep_logs: bool = False, keep_root: str = "saved_logs"):
        self.swept = sweep_stale()
        self.keep_logs = keep_logs
        self.keep_root = Path(keep_root)
        self.dir = Path(tempfile.mkdtemp(prefix=PREFIX))
        (self.dir / PID_FILE).write_text(str(os.getpid()))
        self.kept_to: Path | None = None
        self._closed = False
        atexit.register(self.close)

    def path(self, name: str) -> Path:
        return self.dir / name

    def close(self) -> None:
        """Delete the folder (copying it first if keep_logs). Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self.keep_logs:
            files = [f for f in self.dir.iterdir() if f.name != PID_FILE]
            if files:
                self.kept_to = self.keep_root / datetime.now().strftime("%Y%m%d_%H%M%S")
                self.kept_to.mkdir(parents=True, exist_ok=True)
                for f in files:
                    shutil.copy2(f, self.kept_to / f.name)
        shutil.rmtree(self.dir, ignore_errors=True)
