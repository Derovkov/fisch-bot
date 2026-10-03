"""
fischbot.py -- Fisch fishing macro: cast, lure, and closed-loop reeling.

    python fischbot.py                  # run
    python fischbot.py --max-fish 5     # stop after 5
    python fischbot.py --diagnose       # watch and log, send nothing
    python fischbot.py --focus-mode yield   # usable while you work elsewhere

THE MODEL (what every earlier attempt in this folder got wrong)
--------------------------------------------------------------
Reeling is not click timing. Per the official wiki: holding the input accelerates
the white control slider RIGHT, releasing accelerates it LEFT, and the fish marker
moves on its own. Success is keeping the fish inside the slider. That makes this a
bang-bang position servo, not a click macro -- see fischcontrol.py.

There is no green "good zone". Saturated green pixels inside the bar rect are zero
across every frame of the wiki's own demonstration GIF (minigame_ref/minigame.gif)
and every real capture on this machine. The superseded fisch_macro.py keyed on
green and was detecting the 3D world's foliage.

Phases
    cast   hold LMB to charge, release
    lure   click the Shake button until the fish is lured (faster; optional)
    reel   servo: hold/release to keep the fish inside the slider
    pause  breather, repeat

BACKGROUND USE -- read this before promising it works
----------------------------------------------------
Input is the binding constraint, and no amount of cleverness removes it here:

  * Screen capture only sees Roblox when it is visible and unoccluded. Verified on
    this machine: with another window on top, a screen grab returns THAT window's
    pixels; PrintWindow returns a blank white surface.
  * Roblox ignores mouse messages posted straight to its window. Verified in an
    earlier session here: the messages are accepted but have no effect.

So with Roblox unfocused the macro can neither see the game nor click it. Two real
options:

  --focus-mode pin   (default) Roblox keeps the foreground and the macro runs
                     unattended. You cannot use this PC meanwhile -- your
                     keystrokes would go to Roblox.
  --focus-mode yield Roblox gives up the foreground the moment you take it and
                     resumes when you click back. Other tabs are usable, but the
                     macro pauses mid-reel, so the fish is lost.

There is no third option short of a kernel-level input driver, which is out of
scope. Stated plainly rather than implied by a flag name.

HOTKEYS (work while any window is focused)
    F8  pause / resume      F9  quit      F10  stats
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import random
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastcap import FastGrabber, WinRect, find_roblox_window, focus_window
from typing import Callable

import fischmeasure
from fischcalib import GeometryLearner, precheck
from fischcontrol import ControlConfig, ReelController
from fischrods import (DEFAULT_ROD, ENCHANTS, RodContext, RodProfile, get_rod,
                       reel_enchants)
from fischsession import Session
from fischtrack import (HINT_Y_PAD, PROG_ROWS, TRACK_PROBE, TrackReading,
                        find_progress, read_track)

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
user32.mouse_event.restype = None
user32.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                               ctypes.c_uint, ctypes.c_void_p]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.c_void_p]
user32.GetForegroundWindow.restype = wintypes.HWND

VK_LBUTTON = 0x01

# Consecutive bar readings required to declare the minigame open. On the
# recording, impostor readings (hotbar, catch flash) never ran longer than 2.
START_CONFIRM = 3
# ...when calibration found the idle scene producing bar-like readings.
START_CONFIRM_BUSY = 5

# Progress box search: +-rows around where it was last seen. A full-frame search
# also matched other UI between reels on the recording, so it is never used.
PROG_SEARCH_PAD = 15
# Bar reading says "inside" while progress falls for this long -> re-acquire.
REACQUIRE_AFTER_S = 0.2
TRACE_EVERY_S = 0.1
# Progress peak that counts as a landed fish (box fill quantised to ~1/415).
CAUGHT_PEAK = 0.95


class ProgressTrend:
    """Rising / falling verdict from the progress box's fill over a short window.

    Fill is quantised to ~1/415 per px, and at a -75% progress-speed spot the
    gain rate is only ~3%/s, so the slope is taken over WINDOW_S, not per frame.
    """

    WINDOW_S = 0.35
    MIN_DELTA = 2.5 / 415           # ~2.5px of fill

    def __init__(self) -> None:
        self.hist: list[tuple[float, float]] = []
        self.last_fill: Optional[float] = None
        self.peak: float = 0.0

    def add(self, t: float, fill: float) -> None:
        self.hist.append((t, fill))
        self.last_fill = fill
        self.peak = max(self.peak, fill)
        while self.hist and t - self.hist[0][0] > self.WINDOW_S:
            self.hist.pop(0)

    def verdict(self) -> Optional[bool]:
        """True = filling (fish inside), False = draining, None = unknown/flat."""
        if len(self.hist) < 3 or self.hist[-1][0] - self.hist[0][0] < self.WINDOW_S * 0.6:
            return None
        d = self.hist[-1][1] - self.hist[0][1]
        if d > self.MIN_DELTA:
            return True
        if d < -self.MIN_DELTA:
            return False
        return None


def _save_debug(frame, r: TrackReading, name: str) -> None:
    """Write an annotated crop of the bar region (diagnose --debug)."""
    import cv2

    o = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.rectangle(o, (r.track_x0, r.y0), (r.track_x1, r.y1), (255, 0, 0), 1)
    cv2.rectangle(o, (r.slider_x0, r.y0 - 3), (r.slider_x1, r.y1 + 3), (0, 255, 255), 2)
    x = int(r.marker_x)
    cv2.line(o, (x, r.y0 - 30), (x, r.y1 + 30), (0, 0, 255), 2)
    y0 = max(0, r.y0 - 120)
    cv2.imwrite(name, o[y0:r.y1 + 80])


# ======================================================================================
# Input
# ======================================================================================


class Mouse:
    """OS-level mouse input. Roblox honours this only while it owns the foreground,
    which is exactly why --focus-mode exists."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.down = False
        self.saved: Optional[tuple[int, int]] = None

    def button_held(self) -> bool:
        return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)

    def set_down(self, want: bool) -> None:
        if self.dry_run or want == self.down:
            return
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN if want else MOUSEEVENTF_LEFTUP,
                           0, 0, 0, None)
        self.down = want

    def release(self) -> None:
        """Always release, including on crash and pause paths. A stuck LMB would
        leave the user pressing their own UI for the rest of the session."""
        self.set_down(False)

    def hold(self, seconds: float) -> None:
        self.set_down(True)
        time.sleep(seconds)
        self.set_down(False)

    def click(self, down_ms: int = 45) -> None:
        self.hold(down_ms / 1000.0)

    def cursor(self) -> tuple[int, int]:
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def place_cursor(self, rect: WinRect) -> None:
        """OS clicks fire wherever the cursor happens to be, so it must be over the
        game or the clicks land on whatever is underneath."""
        cx, cy = self.cursor()
        if not (rect.left <= cx < rect.right and rect.top <= cy < rect.bottom):
            self.saved = (cx, cy)
            if not self.dry_run:
                user32.SetCursorPos(rect.left + rect.width // 2,
                                    rect.top + int(rect.height * 0.55))
            time.sleep(0.15)

    def restore_cursor(self) -> None:
        if self.saved and not self.dry_run:
            user32.SetCursorPos(*self.saved)
            self.saved = None


# ======================================================================================
# Focus policy
# ======================================================================================


class FocusPolicy:
    """The two strategies that actually work, given the constraints above."""

    def __init__(self, hwnd: int, mode: str):
        self.hwnd = hwnd
        self.mode = mode
        self.user_has_focus = False

    def ready(self) -> bool:
        mine = user32.GetForegroundWindow() == self.hwnd
        if not mine:
            if self.mode == "yield":
                self.user_has_focus = True
            return False
        if self.user_has_focus:
            self.user_has_focus = False
            focus_window(self.hwnd)
            return True
        return True


# ======================================================================================
# Macro
# ======================================================================================


@dataclass
class MacroConfig:
    cast_hold_s: float = 0.65
    cast_pause_s: float = 1.0
    lure_clicks: int = 14
    lure_interval_s: float = 0.28
    reel_timeout_s: float = 40.0
    # Bar unseen this long = minigame over. Longest true-minigame dropout on the
    # recording was 9 frames (0.15s, catch-flash VFX over the marker).
    end_gap_s: float = 0.5
    # No bar this long after luring = the cast failed or the fish was lost before
    # reeling; give up and recast. Bites came 0-3s after luring at easy spots but
    # ~15s+ at a harder one (measured 2026-10-02) -- 10s recast too early there.
    bite_timeout_s: float = 30.0
    max_fish: int = 0
    focus_mode: str = "pin"
    diagnose: bool = False
    debug: bool = False
    trace: bool = False


class FischBot:
    def __init__(self, cfg: MacroConfig, ccfg: ControlConfig,
                 grabber: FastGrabber, rect: WinRect, hwnd: int, mouse: Mouse,
                 focus: FocusPolicy, rod: Optional[RodProfile] = None,
                 session: Optional[Session] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 enchants: Optional[list[str]] = None):
        self.on_log = on_log
        self.rod = rod or get_rod(DEFAULT_ROD)
        self.enchants: list[str] = list(enchants or [])
        # Every file this run writes goes in the session folder, which is deleted
        # when the run ends (fischsession.py). A bot built without one gets its
        # own, so nothing is ever left in the project folder.
        self._own_session = session is None
        self.session = session or Session()
        self.state = "idle"
        # Live numbers for the UI (read from another thread; plain values only).
        self.live = {"in_reel": False, "fill": None, "inside": None, "elapsed": 0.0}
        self.reel_history: list[dict] = []
        self.learner = GeometryLearner(self.log)
        self.start_confirm = START_CONFIRM
        self.cfg = cfg
        self.ccfg = ccfg
        self.grabber = grabber
        self.rect = rect
        self.hwnd = hwnd
        self.mouse = mouse
        self.focus = focus
        self.ctl = ReelController(ccfg)
        self.running = False
        self.slider_w: Optional[int] = None
        self.cycles = 0
        self.caught = 0
        self.lost = 0
        self.reel_logs: list[str] = []
        self.pending_hint: Optional[TrackReading] = None
        self.last_prog_top: Optional[int] = None    # progress box row, client coords
        self.trace_file = None
        self._trace_t0 = self._trace_last = time.perf_counter()
        if cfg.trace:
            path = self.session.path(f"trace_{datetime.now():%Y%m%d_%H%M%S}.txt")
            self.trace_file = path.open("w", encoding="utf-8")
            self.log("tracing numbers (no images) to the session folder")

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        if self.on_log is not None:
            self.on_log(line)
        else:
            print(line, flush=True)

    def stop(self) -> None:
        """Ask the run to end (UI Stop / hotkey). Releases the mouse at once."""
        self.running = False
        self.mouse.release()

    # -- phases ---------------------------------------------------------------------
    def cast(self) -> None:
        # Never cast into a running minigame. Live (2026-10-02) a reel was declared
        # over while it was still on screen, and the next cast's 0.65s hold plus
        # the shake clicks threw the slider off the fish.
        t_end = time.perf_counter() + 8.0
        waited = False
        while self.running and time.perf_counter() < t_end and self._minigame_on_screen():
            if not waited:
                self.log("minigame still on screen -- waiting before casting")
                waited = True
            time.sleep(0.1)
        self.state = "casting"
        self.log(f"cast (hold {self.cfg.cast_hold_s:.2f}s)")
        self.mouse.hold(self.cfg.cast_hold_s)

    def _trace(self, tag: str) -> None:
        """--trace: one numbers-only line (fischmeasure format) at most every
        TRACE_EVERY_S, from a separate full grab. No images are written."""
        if self.trace_file is None:
            return
        now = time.perf_counter()
        if now - self._trace_last < TRACE_EVERY_S:
            return
        self._trace_last = now
        frame = self.grabber.grab()
        y0, score = fischmeasure.locate_rows(frame)
        r = read_track(frame)
        geo = (f"geo=HIT slider={r.slider_x0}-{r.slider_x1} fish={r.marker_x:.0f}"
               if r else "geo=miss")
        p = None
        if self.last_prog_top is not None:
            p = find_progress(frame, self.last_prog_top - PROG_SEARCH_PAD,
                              self.last_prog_top + PROG_ROWS + PROG_SEARCH_PAD)
        if p is None:
            p = find_progress(frame, y0 + 20, y0 + 100)
        prog = f"prog={p[0]:.3f}@{p[1]}" if p else "prog=none"
        self.trace_file.write(
            f"t={now - self._trace_t0:7.2f} {tag} rows={y0}-{y0 + 30} "
            f"edge={score:5.0f} {geo} {prog} held={self.mouse.down} "
            f"{fischmeasure.sample(frame, y0)}\n")
        self.trace_file.flush()

    def _minigame_on_screen(self) -> bool:
        frame = self.grabber.grab()
        if read_track(frame) is not None:
            return True
        top = self.last_prog_top
        return top is not None and find_progress(
            frame, top - PROG_SEARCH_PAD, top + PROG_ROWS + PROG_SEARCH_PAD) is not None

    def lure(self) -> None:
        """Shake to speed the bite. The wiki notes each click relocates the Shake
        button and that clicking elsewhere can CANCEL the cast, so clicks go to a
        fixed spot in the lower-middle of the viewport where the button lives.
        With Instant Catcher bait luring is near-instant anyway."""
        self.state = "luring"
        self.log(f"luring (up to {self.cfg.lure_clicks} clicks)")
        self.pending_hint = None
        for i in range(self.cfg.lure_clicks):
            if not self.running or not self.focus.ready():
                return
            t_next = (time.perf_counter() + self.cfg.lure_interval_s
                      + random.uniform(-0.04, 0.04))
            self.mouse.click()
            if self.cfg.debug and i % 5 == 0:
                self.log(f"  shake {i + 1}/{self.cfg.lure_clicks}")
            # Stop the moment the bar is up. Live (2026-10-02) the bite often came
            # mid-lure and the remaining shake clicks landed INSIDE the minigame,
            # jerking the slider off the fish.
            r = read_track(self.grabber.grab())
            if r is not None:
                self.pending_hint = r
                self.log(f"  bar up after shake {i + 1} -- stop luring")
                return
            time.sleep(max(0.0, t_next - time.perf_counter()))

    def reel(self) -> bool:
        """Closed-loop reel driven by fischtrack.read_track.

        PHASE is the bar itself: the minigame is up while the track + slider +
        fish marker are on screen. (HUD absence, the old trigger, misses most of
        a reel: on the recording the HUD is back for frames 612-792 of reel 2.)
        Start = START_CONFIRM consecutive readings; end = no reading for
        cfg.end_gap_s. Shorter dropouts (a VFX flash over the marker) keep the
        current input.

        The return value is a proxy: whether the fish was inside the slider for
        most of the reel. The bar alone does not say caught vs escaped.
        """
        self.log("reeling: waiting for the bar")
        t0 = time.perf_counter()
        deadline = t0 + self.cfg.reel_timeout_s
        bite_deadline = t0 + self.cfg.bite_timeout_s
        hint = getattr(self, "pending_hint", None)   # bar already seen in lure()
        self.pending_hint = None
        streak = 0
        in_phase = False
        first_seen = last_seen = last_prog = 0.0
        frames = 0
        trend = ProgressTrend()
        falling_inside_since: Optional[float] = None
        reacquires = 0
        rod_active = False
        self.rod.reset()
        self.live = {"in_reel": False, "fill": None, "inside": None, "elapsed": 0.0}

        while self.running and time.perf_counter() < deadline:
            if not in_phase and hint is None and time.perf_counter() > bite_deadline:
                self.log(f"no bar within {self.cfg.bite_timeout_s:.0f}s -- "
                         "cast probably failed or fish was lost; recasting")
                self.mouse.release()
                return False
            if not self.focus.ready():
                self.mouse.release()
                self.log("focus lost mid-reel -- fish almost certainly lost")
                return False

            self._trace("reel" if in_phase else "wait")
            self.state = "reeling" if in_phase else "waiting for a bite"
            r, now, img, y_off = self._grab_and_read(hint)

            # --- rod's own minigame: the bot plays it (rod.play), no human input;
            # the slider servo pauses while it is on screen ---------------------
            if in_phase and self.rod.has_extra:
                full = self.grabber.grab()
                if self.rod.detect(full):
                    if not rod_active:
                        rod_active = True
                        self.log(f"  {self.rod.name}: rod minigame detected -- "
                                 f"bot is playing it")
                    self.state = "rod minigame"
                    self.rod.play(RodContext(full, now, self.mouse, self.log))
                    last_seen = now          # the reel is still on
                    continue
                if rod_active:
                    rod_active = False
                    hint = None              # re-find the bar from scratch
                    self.log(f"  {self.rod.name}: rod minigame over -- resuming reel")

            # --- progress box: the game's own inside/outside verdict ----------
            p = None
            if r is not None:
                p_lo, p_hi = r.y1 + 20, r.y1 + 70
            elif self.last_prog_top is not None and in_phase:
                p_lo = self.last_prog_top - PROG_SEARCH_PAD
                p_hi = self.last_prog_top + PROG_ROWS + PROG_SEARCH_PAD
            else:
                p_lo = None
            if p_lo is not None:
                p = find_progress(img, p_lo - y_off, p_hi - y_off)
                if p is not None:
                    self.last_prog_top = p[1] + y_off
                    last_prog = now
                    if in_phase:
                        trend.add(now, p[0])
                        self.live["fill"] = p[0]

            if in_phase:
                verdict = trend.verdict()
                self.ctl.stats.note_game(verdict)
                # Bar reading says inside but the game says the fish is out: the
                # reading is wrong. Drop the hint so the next frame does a full
                # search of the bar instead of trusting the tracked position.
                if verdict is False and r is not None and r.fish_inside:
                    if falling_inside_since is None:
                        falling_inside_since = now
                    elif now - falling_inside_since > REACQUIRE_AFTER_S:
                        reacquires += 1
                        if reacquires <= 3 or self.cfg.debug:
                            self.log(f"  progress falling but bar reading says inside "
                                     f"-- re-acquiring bar (#{reacquires})")
                        hint = None
                        r = None
                        falling_inside_since = None
                else:
                    falling_inside_since = None

            if r is not None:
                hint = r
                last_seen = now
                if in_phase:
                    self.learner.feed(img, y_off, r,
                                      p[1] + y_off if p is not None else None)
                if not in_phase:
                    streak += 1
                    if streak == 1:
                        first_seen = now
                    if streak >= self.start_confirm:
                        in_phase = True
                        # Timed from the FIRST sighting, not the confirmation:
                        # live reels with boosts last only ~1.5-2s.
                        self.ctl.begin(first_seen)
                        self.log(f"minigame started: track {r.track_x0}-{r.track_x1} "
                                 f"y{r.y0}-{r.y1}, slider {r.slider_width}px")
                    else:
                        continue
                frames += 1
                self.live.update(in_reel=True, inside=bool(r.fish_inside),
                                 elapsed=now - first_seen)
                want = self.ctl.step(r, now=now)
                if want is not None:
                    self.mouse.set_down(want)
                if self.cfg.debug and frames % 20 == 0:
                    self.log(f"  slider {r.slider_x0}..{r.slider_x1} fish "
                             f"{r.marker_x:.0f} err {r.error:+.0f} "
                             f"v_s {self.ctl.v_slider:+.0f} v_f {self.ctl.v_fish:+.0f} "
                             f"held={self.mouse.down}")
                continue

            if not in_phase:
                streak = 0
                hint = None
                continue
            # Over only when the bar AND the progress box have both been gone for
            # end_gap_s. Bar alone was not enough: against a red/black striped
            # background the bar was lost for >0.5s mid-reel.
            if now - max(last_seen, last_prog) < self.cfg.end_gap_s:
                # Dropout: keep the current input. If the bar has been missing a
                # while, widen to a full search.
                if now - last_seen > 0.15:
                    hint = None
                continue

            self.ctl.step(None, now=now)
            self.mouse.release()
            s = self.ctl.stats
            span = last_seen - first_seen
            rate = f" | {s.frames / span:.0f} readings/s" if span > 0 else ""
            fill = ""
            if trend.last_fill is not None:
                fill = (f" | progress peak {trend.peak:.0%}, last {trend.last_fill:.0%}"
                        f" | {'CAUGHT' if trend.peak >= CAUGHT_PEAK else 'ESCAPED?'}")
            self.log("minigame ended (bar and progress box gone)")
            self.reel_logs.append(s.summary() + rate + fill)
            self.log(s.summary() + rate + fill)
            caught = (trend.peak >= CAUGHT_PEAK if trend.last_fill is not None
                      else s.inside_frac > 0.5)
            self.reel_history.append({
                "caught": bool(caught),
                "peak": round(trend.peak, 3),
                "filling": round(s.game_in / s.game_n, 3) if s.game_n else None,
                "duration": round(span, 1),
                "readings": round(s.frames / span) if span > 0 else 0,
                "input_changes": s.input_changes,
            })
            self.live = {"in_reel": False, "fill": None, "inside": None, "elapsed": 0.0}
            # The box reaching (nearly) full is the catch. "last" alone is not:
            # the box resets right after a catch, so the last reading can be low
            # even for a landed fish (seen in the recording replay).
            if trend.last_fill is not None:
                return trend.peak >= CAUGHT_PEAK
            return s.inside_frac > 0.5 if s.frames else False

        self.mouse.release()
        self.log(f"reel timed out: {self.ctl.stats.summary()}")
        return False

    def _grab_and_read(self, hint: Optional[TrackReading]):
        """Grab + read one frame. With a hint, grab only the rows around the bar.

        A full 1920x1009 grab + search measured ~65ms/frame live, too slow for the
        servo (sim_servo.py degrades past ~40ms). The band is ~4x fewer rows, and
        read_track's hint mode never looks outside it.

        Returns (reading, time, image, image_y_offset) -- the image is passed on
        so the progress box is read from the same grab.
        """
        h = self.grabber.rect.height if self.grabber.rect is not None else None
        if hint is None or h is None or not hasattr(self.grabber, "grab_rows"):
            frame = self.grabber.grab()
            return read_track(frame, hint=hint), time.perf_counter(), frame, 0
        pad = HINT_Y_PAD + TRACK_PROBE + 20       # covers the progress box too
        y_a = max(0, hint.y0 - pad)
        y_b = min(h, hint.y1 + pad)
        band = self.grabber.grab_rows(y_a, y_b)
        now = time.perf_counter()
        local = replace(hint, y0=hint.y0 - y_a, y1=hint.y1 - y_a)
        r = read_track(band, hint=local)
        if r is not None:
            r = replace(r, y0=r.y0 + y_a, y1=r.y1 + y_a)
        return r, now, band, y_a

    # -- main -----------------------------------------------------------------------
    def run(self) -> None:
        self.running = True
        self.mouse.place_cursor(self.rect)
        if self.cfg.diagnose:
            return self.diagnose_loop()

        # Every run assumes a new area: reset geometry, check the window and the
        # idle scene, and re-learn the bar's position on the first reel.
        self.state = "calibrating"
        pre = precheck(self.grabber.grab, self.rect.width, self.rect.height, self.log)
        if pre.busy_scene:
            self.start_confirm = START_CONFIRM_BUSY
        self.log(f"rod: {self.rod.name}"
                 + (f" | enchants: {', '.join(self.enchants)}" if self.enchants else ""))
        for e in reel_enchants(self.enchants):
            # Informational for now: the servo already adapts to slider width
            # (Control) and fish speed; slashes/stuns just pause the fish.
            self.log(f"  enchant {e} changes the reel: {ENCHANTS[e]['effect'][:120]}")

        if self.cfg.focus_mode == "pin":
            self.log("focus mode PIN: Roblox holds the foreground. You cannot use "
                     "this PC while it runs. (F8 pauses if you need to.)")
        else:
            self.log("focus mode YIELD: you can use other tabs. The macro pauses "
                     "when you take focus, so fish will be lost mid-reel.")

        try:
            while self.running:
                if self.cfg.max_fish and self.cycles >= self.cfg.max_fish:
                    self.log(f"reached --max-fish={self.cfg.max_fish}")
                    break
                if not self.focus.ready():
                    time.sleep(0.4)
                    continue

                self.cast()
                time.sleep(self.cfg.cast_pause_s)
                self.lure()
                if self.reel():
                    self.caught += 1
                else:
                    self.lost += 1
                self.cycles += 1
                self.log(f"cycle {self.cycles} done: {self.caught} caught, "
                         f"{self.lost} lost")
                time.sleep(0.6)
        finally:
            self.state = "stopped"
            self.mouse.release()
            self.mouse.restore_cursor()
            if self.trace_file is not None:
                self.trace_file.close()
            self.log(f"stopped -- {self.cycles} cycles, {self.caught} caught, "
                     f"{self.lost} lost")
            if self.reel_logs:
                self.session.path("reel_runs.log").write_text(
                    "\n".join(self.reel_logs), encoding="utf-8")
            if self._own_session:
                self.session.close()

    def diagnose_loop(self) -> None:
        """Watch only, send nothing.

        Prints every fischtrack reading (track, slider, fish marker) plus the
        frame size and timing, in CLIENT coordinates -- which is what the live
        macro sees, unlike the full-desktop recording. Do a full cast and reel by
        hand, then Ctrl+C. Optionally saves annotated frames with --debug.
        """
        self.log("DIAGNOSE: watching only, no input sent. Do a cast + reel.")
        self.log("Ctrl+C when finished.")
        seen = grabs = 0
        hint = None
        t_read = 0.0
        try:
            while True:
                frame = self.grabber.grab()
                grabs += 1
                t0 = time.perf_counter()
                r = read_track(frame, hint=hint)
                t_read += time.perf_counter() - t0
                hint = r
                if r is not None:
                    seen += 1
                    print(f"  frame {frame.shape[1]}x{frame.shape[0]} track "
                          f"{r.track_x0}-{r.track_x1} y{r.y0}-{r.y1} slider "
                          f"{r.slider_x0}-{r.slider_x1} (w={r.slider_width}) fish "
                          f"{r.marker_x:.0f} err {r.error:+.0f} "
                          f"{'IN' if r.fish_inside else 'OUT'}", flush=True)
                    if self.cfg.debug and seen % 30 == 1:
                        _save_debug(frame, r,
                                    str(self.session.path(f"diag_{seen:05d}.png")))
        except KeyboardInterrupt:
            self.log(f"diagnose ended: bar read in {seen}/{grabs} grabs, "
                     f"read_track {t_read / max(grabs, 1) * 1000:.1f}ms/grab")


# ======================================================================================
# Hotkeys and CLI
# ======================================================================================


def _toggle(bot: FischBot) -> None:
    bot.running = not bot.running
    if not bot.running:
        bot.mouse.release()
    bot.log("RESUMED" if bot.running else "PAUSED (F8 to resume)")


def _quit(bot: FischBot) -> None:
    # Graceful: the run loop sees running=False, releases the mouse, writes its
    # summary and the session folder is cleaned up. (Raising SystemExit here only
    # ended the keyboard library's own thread.)
    bot.log("F9 -> stopping")
    bot.stop()


def install_hotkeys(bot: FischBot) -> None:
    import keyboard

    keyboard.add_hotkey("f8", lambda: _toggle(bot))
    keyboard.add_hotkey("f9", lambda: _quit(bot))
    keyboard.add_hotkey("f10", lambda: bot.log(
        f"{bot.cycles} cycles, {bot.caught} caught, {bot.lost} lost, "
        f"slider_w={bot.slider_w}"))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fisch fishing macro: cast, lure, closed-loop reeling.")
    ap.add_argument("--cast-hold", type=float, default=0.65,
                    help="seconds LMB is held to charge the cast")
    ap.add_argument("--lure-clicks", type=int, default=14,
                    help="shake clicks while waiting for the bite")
    ap.add_argument("--lure-interval", type=float, default=0.28,
                    help="seconds between shake clicks")
    ap.add_argument("--reel-timeout", type=float, default=40.0)
    ap.add_argument("--deadband", type=float, default=0.06,
                    help="input deadband as a fraction of slider width")
    ap.add_argument("--lookahead", type=float, default=0.50,
                    help="seconds of velocity prediction in the servo (0 = old "
                         "position-only law; see sim_servo.py)")
    ap.add_argument("--bite-timeout", type=float, default=30.0,
                    help="seconds to wait for a bite before recasting")
    ap.add_argument("--end-gap", type=float, default=0.5,
                    help="seconds without the bar before the reel counts as over")
    ap.add_argument("--max-fish", type=int, default=0, help="stop after N fish")
    ap.add_argument("--focus-mode", choices=("pin", "yield"), default="pin",
                    help="pin: Roblox keeps the foreground (unattended, but you "
                         "cannot use the PC). yield: you may use other tabs, but "
                         "the macro pauses when you take focus and fish are lost.")
    ap.add_argument("--diagnose", action="store_true",
                    help="watch and log detection only; send no input")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide but never send input")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--trace", action="store_true",
                    help="log bar/progress measurements (numbers only) ~10x/s to "
                         "the session folder, for diagnosing new spots")
    ap.add_argument("--rod", default=DEFAULT_ROD,
                    help="equipped rod, as named on the wiki (decides how its own "
                         "minigame is played)")
    ap.add_argument("--enchants", default="",
                    help="comma-separated enchants on the rod, e.g. "
                         "\"Starforged Spirit,Crested,Paradise\"")
    ap.add_argument("--keep-logs", action="store_true",
                    help="copy the session's logs to saved_logs/ before the "
                         "temporary folder is deleted")
    args = ap.parse_args(argv)

    session = Session(keep_logs=args.keep_logs)
    try:
        bot = create_bot(
            MacroConfig(
                cast_hold_s=args.cast_hold,
                lure_clicks=args.lure_clicks,
                lure_interval_s=args.lure_interval,
                reel_timeout_s=args.reel_timeout,
                end_gap_s=args.end_gap,
                bite_timeout_s=args.bite_timeout,
                max_fish=args.max_fish,
                focus_mode=args.focus_mode,
                diagnose=args.diagnose,
                debug=args.debug,
                trace=args.trace,
            ),
            ControlConfig(deadband_frac=args.deadband, lookahead_s=args.lookahead),
            rod=get_rod(args.rod), session=session,
            dry_run=args.dry_run or args.diagnose, on_log=None,
            enchants=[e.strip() for e in args.enchants.split(",") if e.strip()])

        try:
            import keyboard
            install_hotkeys(bot)
            print("hotkeys: F8 pause/resume, F9 stop, F10 stats")
        except Exception as exc:
            print(f"hotkeys unavailable ({exc}); use Ctrl+C instead")

        try:
            bot.run()
        except KeyboardInterrupt:
            print("interrupted")
        finally:
            bot.mouse.release()
            bot.mouse.restore_cursor()
            bot.grabber.close()
    finally:
        session.close()
        if session.kept_to:
            print(f"logs kept in {session.kept_to}")
    return 0


class NoRobloxWindow(RuntimeError):
    pass


def create_bot(cfg: MacroConfig, ccfg: ControlConfig, rod: RodProfile,
               session: Session, dry_run: bool = False,
               on_log: Optional[Callable[[str], None]] = None,
               enchants: Optional[list[str]] = None) -> FischBot:
    """Find and focus Roblox, then build a bot on it. Shared by CLI and UI."""
    say = on_log or print
    win = find_roblox_window()
    if win is None:
        raise NoRobloxWindow("No Roblox window found. Start Fisch in windowed mode.")
    hwnd, rect, title = win
    if not focus_window(hwnd):
        say("warning: could not bring Roblox to the foreground")
    # Re-read the client rect after focusing: if Roblox was minimised, the rect
    # read above is meaningless, and the grabber must match the real window.
    win = find_roblox_window()
    if win is not None:
        hwnd, rect, title = win
    say(f"{title!r} client={rect.width}x{rect.height} at ({rect.left},{rect.top})")
    return FischBot(cfg, ccfg, FastGrabber(rect), rect, hwnd, Mouse(dry_run=dry_run),
                    FocusPolicy(hwnd, cfg.focus_mode), rod=rod, session=session,
                    on_log=on_log, enchants=enchants)


if __name__ == "__main__":
    raise SystemExit(main())