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


# --- grinding its buffs (Misc tab) -----------------------------------------------
# From the user (2026-10-05): a Misc tab to grind the Lullaby's buffs, picking
# the buff with the mode buttons on its card in the Equipment Bag (a column of
# six down the card's right side, fischequip.find_lullaby_modes), and switching
# to another buff after some time. Every metronome hit adds 2.5s of the current
# mode's buff (the reel already aims for hits, see Lullaby.gate), so fishing
# with a mode on IS grinding that buff.
#
# Modes and buffs (fischipedia.org/wiki/Lullaby, via search, 2026-10-05), in
# the wiki's order -- assumed to be the buttons' order, top to bottom.
MODES = (
    {"name": "Resistant Composition", "buff": "Resistant",
     "effect": "+20% Resilience, +0.05 Control"},
    {"name": "Quickening Symphony", "buff": "Quickening",
     "effect": "+20% Lure Speed, +20% Progress Speed"},
    {"name": "Strengthening Melody", "buff": "Strengthening",
     "effect": "+75,000 Max Kg, +75 Line Distance, +50% XP, +4 Disturbance"},
    {"name": "Fortuitous Harmony", "buff": "Fortuitous",
     "effect": "+40% Luck, +10% Fish Size"},
    {"name": "Prismatic Sinfonia", "buff": "Prismatic",
     "effect": "+12% Prismatic, +3% Mythical/Shiny/Sparkling, +20% Luck, "
               "+10% Lure Speed, +10% Resilience"},
    {"name": "Serene Hymn", "buff": "Serenity",
     "effect": "+45% Resilience, +10% Serene chance"},
)
MODE_NAMES = tuple(m["name"] for m in MODES)
ROD = "Lullaby"
MAX_STEPS = 12
STEP_MINUTES = (1, 600)
RETRY_S = 120               # a mode that could not be set: try again after


def clean_lullaby(raw) -> dict:
    """Validated Misc > Lullaby buffs settings: {"enabled", "steps": [{"mode",
    "minutes"}]} (unknown modes dropped, minutes clamped)."""
    raw = raw if isinstance(raw, dict) else {}
    steps = []
    for st in raw.get("steps", []) if isinstance(raw.get("steps"), list) else []:
        if not isinstance(st, dict) or st.get("mode") not in MODE_NAMES:
            continue
        try:
            minutes = float(st.get("minutes", 15))
        except (TypeError, ValueError):
            minutes = 15.0
        lo, hi = STEP_MINUTES
        steps.append({"mode": st["mode"], "minutes": round(min(hi, max(lo, minutes)))})
    return {"enabled": bool(raw.get("enabled", False)), "steps": steps[:MAX_STEPS]}


class LullabyBuffs:
    """Keeps the Lullaby on the buff the schedule wants: the first step's mode
    at the start of a run, then each step's mode for its minutes, round and
    round (one step: stay on it). Runs between casts only (worker thread, rod
    reeled in); the mode is set with the bag's buttons, never mid-reel. The
    game does not show the mode in the bag, so the bot remembers what it set."""

    def __init__(self, settings: dict, log, clock=None):
        import time
        self.cfg = clean_lullaby(settings)
        self.log = log
        self.clock = clock or time.time
        self.index = 0
        self.mode: Optional[str] = None      # what the bot last set
        self.since: Optional[float] = None   # ... and when
        self.retry_at = 0.0
        self.status = ""
        self.switches = 0

    @property
    def steps(self) -> list[dict]:
        return self.cfg["steps"]

    @property
    def active(self) -> bool:
        return self.cfg["enabled"] and bool(self.steps)

    def update(self, settings: dict) -> None:
        """New settings mid-run (saved on the tab). Stays on the current step
        (and its timer) if it is still in the list."""
        self.cfg = clean_lullaby(settings)
        steps = self.steps
        if not steps:
            self.index = 0
            return
        if self.index < len(steps) and steps[self.index]["mode"] == self.mode:
            return
        k = next((i for i, st in enumerate(steps) if st["mode"] == self.mode), None)
        if k is None:
            self.index, self.since = 0, None          # switch at the next cast
        else:
            self.index = k
        self.retry_at = 0.0

    def skip(self) -> None:
        """The next step's buff, at the next cast."""
        if self.steps:
            self.index = (self.index + 1) % len(self.steps)
            self.since, self.retry_at = None, 0.0

    def due(self, now: float) -> Optional[int]:
        """The step whose mode must be set now, or None. Moves on to the next
        step when the current one's time is up."""
        steps = self.steps
        if not steps:
            return None
        self.index %= len(steps)
        st = steps[self.index]
        if (self.since is not None and len(steps) > 1
                and now - self.since >= st["minutes"] * 60):
            nxt = (self.index + 1) % len(steps)
            self.log(f"lullaby: {st['minutes']:g} min of {self._buff(st['mode'])} done "
                     f"-> {self._buff(steps[nxt]['mode'])}")
            self.index, self.since = nxt, None
            st = steps[nxt]
        if st["mode"] == self.mode and self.since is not None:
            return None
        if st["mode"] == self.mode:              # same mode as the last step: no press
            self.since = now
            return None
        return self.index

    @staticmethod
    def _buff(mode: str) -> str:
        return next((m["buff"] for m in MODES if m["name"] == mode), mode)

    def between_casts(self, bot) -> None:
        if not self.active:
            return
        if bot.rod.name != ROD:
            self.status = f"waiting -- {bot.rod.name} is in use, not the {ROD}"
            return
        now = self.clock()
        if self.retry_at > now:
            return
        k = self.due(now)
        if k is None:
            self._left_status(now)
            return
        mode = self.steps[k]["mode"]
        result = self.press(bot, MODE_NAMES.index(mode))
        if result is None:                        # not attempted (paused/stopped)
            return
        if result.startswith("error"):
            self.retry_at = now + RETRY_S
            self.status = f"could not set {self._buff(mode)}: {result[6:]}"
            self.log(f"lullaby: {self.status} -- next try in {RETRY_S // 60} min")
            return
        self.mode, self.since = mode, self.clock()
        self.switches += 1
        self.log(f"lullaby: {mode} set ({self._buff(mode)} buff)"
                 + ("" if result == "changed" else " -- pressed; the button did not visibly change"))
        self._left_status(self.since)

    def _left_status(self, now: float) -> None:
        st = self.steps[self.index]
        buff = self._buff(st["mode"])
        if len(self.steps) == 1 or self.since is None:
            self.status = f"grinding {buff}"
            return
        left = max(0.0, st["minutes"] * 60 - (now - self.since))
        nxt = self._buff(self.steps[(self.index + 1) % len(self.steps)]["mode"])
        self.status = f"grinding {buff} -- {left / 60:.0f} min, then {nxt}"

    def press(self, bot, index: int) -> Optional[str]:
        """Open the bag, press mode button `index`, close it, make sure the rod
        is still in hand. "changed" / "pressed", "error: why", or None when
        not attempted."""
        import time
        from fischequip import (EquipmentMenu, MenuCancelled, MenuError, WinInput,
                                ensure_rod_held)

        if not bot.running or not bot.focus.ready() or not bot._refresh_window():
            return None
        if bot.mouse.dry_run:
            self.log(f"lullaby: dry run -- would press mode button {index + 1}")
            return "pressed"
        bot.mouse.release()
        prev, bot.state = bot.state, "setting the Lullaby's mode"
        self.log(f"lullaby: setting {MODE_NAMES[index]} (Equipment Bag)")
        try:
            with EquipmentMenu(bot.grabber.grab, bot.rect, self.log,
                               cancelled=lambda: not bot.running) as menu:
                result = menu.lullaby_mode(index, ROD)
            time.sleep(0.3)                      # the bag's close animation
            ensure_rod_held(bot.grabber.grab, bot.rod.name, [], bot.enchants,
                            WinInput(), bot._input_ready, self.log)
            return result
        except MenuCancelled:
            return None
        except MenuError as exc:
            return f"error {exc}"
        except Exception as exc:                 # OCR missing etc.: never kill the run
            return f"error {exc!r}"
        finally:
            bot.state = prev
            bot.recentre()
            time.sleep(0.2)

    def view(self) -> dict:
        left = None
        if self.since is not None and self.steps and len(self.steps) > 1:
            st = self.steps[self.index % len(self.steps)]
            left = max(0.0, st["minutes"] * 60 - (self.clock() - self.since))
        return {"enabled": self.cfg["enabled"], "index": self.index, "mode": self.mode,
                "left_s": left, "status": self.status, "switches": self.switches}
