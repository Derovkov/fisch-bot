"""Lullaby's metronome: is the pendulum over a section right now?

From the wiki (https://fischipedia.org/wiki/Lullaby, 2026-10-05) and its six
metronome GIFs (one per mode, Q switches):
  * a black half-ring sits on the reel bar's centre with light SECTIONS on it
    (which and how many depend on the mode); a wand pivots at the ring's centre
    and swings across it;
  * clicking while the wand is over a section is a hit (+1-2% progress, a
    little control, +2.5s of the mode's buff); clicking anywhere else is a
    miss: -5% progress, -1s buff. Not clicking at all costs nothing.
The reel itself is played with mouse presses, so every press is also a
metronome click: the bot should press when the wand is over a section.

Geometry (measured on the GIFs, as fractions of the track's width W): the ring's
centre is the track's centre, RING_DY above the track's top; the ring runs from
RING_IN to RING_OUT; the wand reaches out to ~0.25 W.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

RING_IN, RING_OUT = 0.161, 0.213    # x track width
RING_DY = 0.008                     # ring centre above the track top, x track width
WAND_R = (0.222, 0.245)             # just outside the ring: only the wand moves there
ANGLES = np.arange(0, 181, 1.0)     # degrees: 0 = right, 90 = up, 180 = left
SECTION_LIGHT = 120                 # ring brightness (mean of channels) of a section
HISTORY = 24                        # frames of ring brightness kept (the wand hides parts)
WAND_MIN = 40                       # whiteness above that angle's usual level


def _sample(frame: np.ndarray, cx: float, cy: float, radii: np.ndarray) -> np.ndarray:
    """RGB at each (angle, radius): array (len(ANGLES), len(radii), 3)."""
    h, w = frame.shape[:2]
    t = np.radians(ANGLES)[:, None]
    xs = np.clip(np.round(cx + radii[None, :] * np.cos(t)), 0, w - 1).astype(int)
    ys = np.clip(np.round(cy - radii[None, :] * np.sin(t)), 0, h - 1).astype(int)
    return frame[ys, xs].astype(np.float32)


def _spans(on: np.ndarray) -> list[tuple[float, float]]:
    out, start = [], None
    for i, v in enumerate(np.r_[on, False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((float(ANGLES[start]), float(ANGLES[i - 1])))
            start = None
    return out


class Metronome:
    """Fed the frame and the track each reel frame; says where the sections and
    the wand are. Sections are learned over the last HISTORY frames (the wand
    covers part of the ring in each), the wand against each angle's usual look."""

    def __init__(self):
        self.ring_hist: list[np.ndarray] = []
        self.wand_bg: Optional[np.ndarray] = None
        self.sections: list[tuple[float, float]] = []
        self.wand: Optional[float] = None

    def geometry(self, x0: int, x1: int, top: int) -> tuple[float, float, float]:
        W = x1 - x0
        return (x0 + x1) / 2, top - RING_DY * W, W

    def update(self, frame: np.ndarray, x0: int, x1: int, top: int) -> None:
        cx, cy, W = self.geometry(x0, x1, top)
        ring = _sample(frame, cx, cy, np.linspace(RING_IN + 0.01, RING_OUT - 0.01, 5) * W)
        light = np.median(ring.mean(2), axis=1)               # per angle
        self.ring_hist = (self.ring_hist + [light])[-HISTORY:]
        usual = np.median(np.array(self.ring_hist), axis=0)
        self.sections = [s for s in _spans(usual > SECTION_LIGHT) if s[1] - s[0] >= 2]
        outer = _sample(frame, cx, cy, np.linspace(*WAND_R, 4) * W)
        white = outer.min(2) - (outer.max(2) - outer.min(2))   # bright AND grey
        white = np.max(white, axis=1)
        white = np.convolve(white, np.ones(5) / 5, mode="same")
        if self.wand_bg is None:                   # first frame: the typical angle
            self.wand_bg = np.full_like(white, np.median(white))
        lift = white - self.wand_bg
        self.wand_bg += 0.1 * (white - self.wand_bg)
        i = int(np.argmax(lift))
        self.wand = float(ANGLES[i]) if lift[i] >= WAND_MIN else None

    def over_section(self, margin: float = 3.0) -> Optional[bool]:
        """True/False: the wand is / isn't over a section; None: wand not seen."""
        if self.wand is None:
            return None
        return any(a - margin <= self.wand <= b + margin for a, b in self.sections)


# --- playing it ----------------------------------------------------------------
from fischrods import SPECIAL, RodProfile  # noqa: E402

PRESS_WAIT_MAX_S = 0.35     # a press the reel wants waits at most this long for a section
METRO_ABOVE = 0.27          # the metronome reaches this far above the track, x its width


class Lullaby(RodProfile):
    """The normal reel, with every press timed to the metronome: presses the
    reel wants wait (briefly) for the wand to be over a section, and a free tap
    is made when the wand enters a section while the mouse is up."""
    name = "Lullaby"
    wiki = "https://fischipedia.org/wiki/Lullaby"
    has_extra = False

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.metro = Metronome()
        self.wait_since: Optional[float] = None
        self.was_over = False
        self.taps = self.on_beat = self.held_back = self.forced = 0

    def gate(self, region: np.ndarray, x0: int, x1: int, top: int, now: float,
             want: Optional[bool], held: bool) -> tuple[Optional[bool], bool]:
        """(input to send, tap now?). `region` holds the metronome; `top` is the
        track's top row in it. `want` is what the reel controller asked for."""
        self.metro.update(region, x0, x1, top)
        over = self.metro.over_section()
        entered = over is True and not self.was_over
        self.was_over = over is True
        if want is True and not held:
            if over is False:
                if self.wait_since is None:
                    self.wait_since = now
                if now - self.wait_since < PRESS_WAIT_MAX_S:
                    self.held_back += 1
                    return None, False               # wait for a section
                self.forced += 1                     # the fish matters more: a miss
            elif over is True:
                self.on_beat += 1
            self.wait_since = None
            return True, False
        if want is not True:
            self.wait_since = None
        if entered and not held and want is not True:
            self.taps += 1
            return want, True                        # a free hit
        return want, False

    def summary(self) -> str:
        return (f"metronome: {self.on_beat} presses on a section, {self.taps} extra taps, "
                f"{self.forced} presses off-beat (the reel needed them), "
                f"sections at {[(round(a), round(b)) for a, b in self.metro.sections]} deg")


SPECIAL["Lullaby"] = Lullaby()
