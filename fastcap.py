"""
Fast screen capture for the Fisch macro.

Why this exists
---------------
The old macro used PIL.ImageGrab, which takes ~40-60 ms per grab on a 1920x1009
region. The reel minigame's marker crosses the good zone in well under a second,
so a 50 ms capture plus 10 ms of Python is a sampling rate that can miss the
window entirely. mss grabs straight from the DXGI/GDI surface with a much lower
per-frame cost, which buys the frame rate the controller actually needs.

Everything here is measurement infrastructure: it makes no decisions and sends
no input.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# Titles that must never be mistaken for the game client. A Firefox tab titled
# "Fisch | Play on Roblox - Mozilla Firefox" matches a naive 'roblox' substring
# search, which would target the browser instead of the game.
BROWSER_TITLES = (
    "mozilla firefox", "chrome", "google chrome", "microsoft edge", "msedge",
    "opera", "brave", "vivaldi", "safari", "internet explorer",
)
GAME_TITLE_HINTS = ("roblox", "robloxplayer")


@dataclass
class WinRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _is_game_window(title: str) -> bool:
    low = title.lower()
    if any(b in low for b in BROWSER_TITLES):
        return False
    return any(h in low for h in GAME_TITLE_HINTS)


def client_rect(hwnd: int) -> Optional[WinRect]:
    """Current client bounds of this window; never move/resize it."""
    r, pt = wintypes.RECT(), wintypes.POINT(0, 0)
    if (user32.IsIconic(hwnd) or not user32.GetClientRect(hwnd, ctypes.byref(r))
            or not user32.ClientToScreen(hwnd, ctypes.byref(pt))):
        return None
    rect = WinRect(pt.x, pt.y, pt.x + r.right, pt.y + r.bottom)
    return rect if rect.width > 200 and rect.height > 200 else None


def find_roblox_window() -> Optional[tuple[int, WinRect, str]]:
    """Return (hwnd, client rect in screen coords, title) for the Roblox client."""
    found: list[tuple[int, WinRect, str]] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title or not _is_game_window(title):
            return True
        r = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(r)):
            return True
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        rect = WinRect(r.left + pt.x, r.top + pt.y, r.right + pt.x, r.bottom + pt.y)
        if rect.width > 200 and rect.height > 200:
            found.append((hwnd, rect, title))
        return True

    user32.EnumWindows(proc(cb), 0)
    found.sort(key=lambda t: t[1].area, reverse=True)
    return found[0] if found else None


class FastGrabber:
    """Repeated grabs of one fixed screen rectangle, returning HxWx3 uint8 RGB."""

    def __init__(self, rect: WinRect):
        import mss

        self.rect = rect
        self._mss = mss.mss()
        self._mon = self._mss.monitors[0]
        self.monitor = {
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
        }
        self.grabs = 0

    def reframe(self, rect: WinRect) -> None:
        """Update capture bounds on this capture thread after a window change."""
        self.rect = rect
        self.monitor = {"left": rect.left, "top": rect.top,
                        "width": rect.width, "height": rect.height}

    def grab(self) -> np.ndarray:
        shot = self._mss.grab(self.monitor)
        # mss returns BGRA; converting straight to RGB is essential because a
        # silent channel swap destroys every colour threshold in the detector.
        a = np.asarray(shot, dtype=np.uint8)
        self.grabs += 1
        return np.ascontiguousarray(a[..., [2, 1, 0]])

    def grab_rows(self, y0: int, y1: int) -> np.ndarray:
        """Grab only a horizontal band -- the reel bar never moves vertically, so
        this is roughly an order of magnitude cheaper than a full-frame grab."""
        m = dict(self.monitor)
        m["top"] = self.rect.top + y0
        m["height"] = max(1, y1 - y0)
        shot = self._mss.grab(m)
        a = np.asarray(shot, dtype=np.uint8)
        return np.ascontiguousarray(a[..., [2, 1, 0]])

    def close(self) -> None:
        try:
            self._mss.close()
        except Exception:
            pass


def focus_window(hwnd: int) -> bool:
    """Bring a window forward, working around Windows' foreground lock.

    Windows refuses SetForegroundWindow from a process that does not own the
    foreground. Attaching to the current foreground thread's input queue grants
    the right to set it, which is why the AttachThreadInput call is here.
    """
    # Only un-minimise. SW_RESTORE on a MAXIMISED window un-maximises it, which
    # resized Roblox and left the grabber capturing the old client rect.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    fg = user32.GetForegroundWindow()
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tid = user32.GetWindowThreadProcessId(fg, None)
    if tid and tid != cur:
        user32.AttachThreadInput(cur, tid, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.AttachThreadInput(cur, tid, False)
    time.sleep(0.12)
    return user32.GetForegroundWindow() == hwnd


if __name__ == "__main__":
    import statistics

    win = find_roblox_window()
    if win is None:
        raise SystemExit("no Roblox window found")
    hwnd, rect, title = win
    print(f"{title!r} client={rect.width}x{rect.height} at ({rect.left},{rect.top})")
    g = FastGrabber(rect)
    g.grab()

    for label, fn in (("full frame", lambda: g.grab()),
                      ("band y880..920", lambda: g.grab_rows(880, 920))):
        ts = []
        for _ in range(60):
            t = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t)
        ts.sort()
        print(f"  {label:16s} median {statistics.median(ts)*1000:6.2f} ms"
              f"   p90 {ts[int(len(ts)*0.9)]*1000:6.2f} ms")
    g.close()
