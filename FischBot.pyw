"""Fisch bot launcher: opens just the app window -- no terminal.

Double-click this file (or the "Fisch bot" shortcut Install.bat makes). .pyw
files run with pythonw.exe, which has no console. Without a console, errors
would vanish, so a failed start shows a message box (and tmp/startup.log).
Only one copy runs at a time: starting it again brings the open window up.
"""
import ctypes
from pathlib import Path
import sys
import traceback

HERE = Path(__file__).resolve().parent
LOG = HERE / "tmp" / "startup.log"
TITLE = "Fisch bot"


def message(text: str, icon: int = 0x10) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)


def already_running() -> bool:
    """A named mutex marks the running copy; if it exists, raise that window."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\FischBotApp")
    if ctypes.windll.kernel32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
        return False
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    if hwnd:
        u.ShowWindow(hwnd, 9)                                  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
    return True


def main() -> None:
    LOG.parent.mkdir(exist_ok=True)
    # pythonw has no console: keep library output in a small log, newest run only.
    out = open(LOG, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = out
    if already_running():
        return
    sys.path.insert(0, str(HERE))
    try:
        import fischui
    except ModuleNotFoundError as exc:
        traceback.print_exc()
        message(f"The Fisch bot is missing a part it needs ({exc.name}).\n\n"
                f"Double-click Install.bat in\n{HERE}\nonce, then start the bot again.")
        return
    except Exception:
        traceback.print_exc()
        message(f"The Fisch bot could not start:\n\n{traceback.format_exc(limit=3)}\n"
                f"Details: {LOG}")
        return
    try:
        fischui.main()
    except Exception:
        traceback.print_exc()
        message(f"The Fisch bot stopped with an error:\n\n{traceback.format_exc(limit=3)}\n"
                f"Details: {LOG}")


if __name__ == "__main__":
    main()
