"""
Per-run calibration. Every run assumes it may be in a new area.

1. precheck(), before the first cast:
     * reset all geometry to the measured defaults (nothing carries over);
     * check the Roblox client size against the one everything was measured on;
     * watch the idle scene for ~1s. If the empty scene already produces bar or
       progress-box readings, the area is "busy" and the bot requires more
       consecutive readings before it believes a reel has started.
2. GeometryLearner, during the first reel(s):
     * measures the track's actual end columns and the progress box's offset
       below the track from the first good readings, and adopts them only if
       they are consistent and close to the defaults.

The reader itself (fischtrack.read_track) already re-derives its slider/track
split on every frame, so there are no colour thresholds to recalibrate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

import fischtrack as ft

MEASURED_CLIENT = (1920, 1009)  # client size all geometry was measured at
IDLE_FRAMES = 15
LEARN_SAMPLES = 15
MAX_SHIFT_PX = 25               # learned ends must be this close to the defaults
MAX_MAD_PX = 2.0                # ...and this consistent across samples


@dataclass
class PrecheckResult:
    busy_scene: bool = False
    notes: list[str] = field(default_factory=list)


def precheck(grab: Callable[[], np.ndarray], width: int, height: int,
             log: Callable[[str], None]) -> PrecheckResult:
    res = PrecheckResult()
    ft.reset_geometry()
    if (width, height) != MEASURED_CLIENT:
        msg = (f"window is {width}x{height}; the bar was measured at "
               f"{MEASURED_CLIENT[0]}x{MEASURED_CLIENT[1]}. Positions scale with "
               f"width; the first reel will re-measure them.")
        if width != MEASURED_CLIENT[0]:
            msg += " If reels are not detected, maximise the Roblox window."
        res.notes.append(msg)

    bar_hits = prog_hits = 0
    for _ in range(IDLE_FRAMES):
        f = grab()
        bar_hits += ft.read_track(f) is not None
        prog_hits += ft.find_progress(f) is not None
        time.sleep(0.05)
    if bar_hits >= 2:
        res.busy_scene = True
        res.notes.append(
            f"idle scene looks bar-like in {bar_hits}/{IDLE_FRAMES} frames (or a reel "
            f"is already on screen) -- requiring more confirmations to start a reel")
    elif prog_hits >= 2:
        res.notes.append(f"idle scene has a progress-box-like shape in "
                         f"{prog_hits}/{IDLE_FRAMES} frames (not used before a reel)")
    for n in res.notes:
        log("calibration: " + n)
    if not res.notes:
        log("calibration: window size and idle scene OK")
    return res


def _edge_col(prof: np.ndarray, guess: int, left: bool) -> Optional[int]:
    """Column of the track's end nearest `guess`: first track column (left) or
    last track column (right), by the strongest brightness step."""
    best, best_x = 0.0, None
    for x in range(max(5, guess - MAX_SHIFT_PX), min(len(prof) - 5, guess + MAX_SHIFT_PX + 1)):
        if left:
            step = abs(prof[x - 4:x].mean() - prof[x:x + 4].mean())
        else:
            step = abs(prof[x + 1:x + 5].mean() - prof[x - 3:x + 1].mean())
        if step > best:
            best, best_x = step, x
    return best_x


class GeometryLearner:
    """Feed it good readings from the first reel; it adopts measured geometry once."""

    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.x0s: list[int] = []
        self.x1s: list[int] = []
        self.dys: list[int] = []
        self.done_x = self.done_dy = False
        self.fed = 0

    @property
    def done(self) -> bool:
        return self.done_x and self.done_dy

    def feed(self, img: np.ndarray, y_off: int, r, prog_top: Optional[int]) -> None:
        if self.done or r is None:
            return
        self.fed += 1
        w = img.shape[1]
        if not self.done_x:
            a, b = r.y0 + 6 - y_off, r.y1 - 5 - y_off
            if 0 <= a < b <= img.shape[0]:
                prof = np.median(img[a:b].astype(np.int16).sum(2), axis=0)
                x0 = _edge_col(prof, r.track_x0, left=True)
                x1 = _edge_col(prof, r.track_x1, left=False)
                if x0 is not None and x1 is not None:
                    self.x0s.append(x0)
                    self.x1s.append(x1)
            if len(self.x0s) >= LEARN_SAMPLES:
                self._adopt_x(w)
        # The box is below the screen while the bar slides in, so its offset keeps
        # being collected after the track ends are settled.
        if not self.done_dy:
            if prog_top is not None:
                self.dys.append(prog_top - r.y1)
            if len(self.dys) >= LEARN_SAMPLES:
                self._adopt_dy()
            elif self.fed >= LEARN_SAMPLES * 20:
                self.done_dy = True
                self.log(f"calibration: progress box seen in only {len(self.dys)} "
                         f"readings -- keeping its default offset")

    @staticmethod
    def _stat(v: list[int]) -> tuple[int, float]:
        a = np.array(v)
        m = float(np.median(a))
        return int(round(m)), float(np.median(np.abs(a - m)))

    def _adopt_x(self, w: int) -> None:
        self.done_x = True
        stat = self._stat
        x0, mad0 = stat(self.x0s)
        x1, mad1 = stat(self.x1s)
        d0, d1 = ft.track_x(w)
        if mad0 <= MAX_MAD_PX and mad1 <= MAX_MAD_PX and \
                abs(x0 - d0) <= MAX_SHIFT_PX and abs(x1 - d1) <= MAX_SHIFT_PX:
            ft.set_geometry(track_x_frac=(x0 / w, x1 / w))
            self.log(f"calibration: track ends measured at x{x0}-{x1} "
                     f"(default {d0}-{d1}) -- adopted")
        else:
            self.log(f"calibration: track-end measurements inconsistent "
                     f"(x{x0}+-{mad0:.0f}, x{x1}+-{mad1:.0f}) -- keeping defaults")

    def _adopt_dy(self) -> None:
        self.done_dy = True
        dy, mad = self._stat(self.dys)
        if mad <= MAX_MAD_PX and abs(dy - ft.DEFAULT_PROG_TOP_DY) <= 10:
            ft.set_geometry(prog_top_dy=dy)
            self.log(f"calibration: progress box {dy}px below the track -- adopted")
        else:
            self.log(f"calibration: progress box offset {dy}+-{mad:.0f}px "
                     f"inconsistent -- keeping {ft.DEFAULT_PROG_TOP_DY}")
