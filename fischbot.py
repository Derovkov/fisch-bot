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

import numpy as np

from fastcap import FastGrabber, WinRect, find_roblox_window, focus_window
from typing import Callable

import fischmeasure
from fischcalib import GeometryLearner, precheck
from fischcatch import CatchNotice, CatchWatch
from fischcontrol import ControlConfig, ReelController
from fischrods import (DEFAULT_ROD, ENCHANTS, RodContext, RodProfile, get_rod,
                       reel_enchants)
from fischsession import Session
from fischtrack import (EDGE_MIN, GEO_ROWS, HINT_Y_PAD, PROG_ROWS, TRACK_PROBE,
                        ProgressPolarity, SkinTracker, TrackReading, current_scale, edge_scores,
                        find_progress, lock_scale, locked_scale, prog_top_dy, px,
                        read_track, scales, set_client, track_x)

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
# Pixel sizes here are at UI scale 1.0 (fischtrack.px scales them).
PROG_SEARCH_PAD = 15
# Bar reading says "inside" while progress falls for this long -> re-acquire.
REACQUIRE_AFTER_S = 0.2
# ...when the SkinTracker reads the bar (non-default skins): it is accurate to
# ~1px, so a short disagreement is the fish on the slider's edge, not a bad read.
REACQUIRE_TRACKER_S = 0.5
# SkinTracker unable to read the bar this long: drop it, re-find the bar from scratch.
TRACKER_GIVE_UP_S = 0.6
TRACE_EVERY_S = 0.1
# --trace bar crops (see FischBot._trace_crop): unreadable bars / readable bars.
TRACE_CROP_MISS_S, TRACE_CROP_MISS_MAX = 0.5, 60
TRACE_CROP_HIT_S, TRACE_CROP_HIT_MAX = 3.0, 10
# Recent slider widths whose median steadies the colour-agnostic slider reading.
SLIDER_W_WINDOW = 15
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
        self.mutations: dict[str, int] = {}     # this run's caught mutations
        # Quest tracker (fischquest.py): read at the start and after every cast.
        self.track_quests = True
        self.owned_rods: list[str] = []         # for the quest plan (Rods page)
        self.quests: list = []
        self.quest_view: Optional[dict] = None   # for the UI's Quests tab
        self._quest_state: Optional[dict] = None
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
        self.progress_polarity = ProgressPolarity()
        self.catch_watch = CatchWatch(self.rod.name)
        self._catch_warned = False
        self.on_cycle_boundary: Optional[Callable[[], None]] = None
        # Useables tab (fischuse.Useables): set by the UI before run()
        self.useables = None
        # The rod in the player's hands, as far as this run knows: the one picked
        # for Start, then whatever equip_rod takes out of the hotbar.
        self._held_rod = self.rod.name
        # Everything log() says also goes to the session folder (kept with the
        # other logs when Keep logs is on): the switch / equip messages were
        # only on screen, so a failed switch could not be looked at afterwards.
        try:
            self._log_file = self.session.path("bot.log").open("a", encoding="utf-8")
        except (OSError, AttributeError):
            self._log_file = None
        self.trace_file = None
        self._trace_t0 = self._trace_last = time.perf_counter()
        self._slider_widths: list[int] = []     # this reel's slider widths (px)
        self._crop_n = {"hit": 0, "miss": 0}
        self._crop_last = {"hit": -1e9, "miss": -1e9}
        if cfg.trace:
            path = self.session.path(f"trace_{datetime.now():%Y%m%d_%H%M%S}.txt")
            self.trace_file = path.open("w", encoding="utf-8")
            self.log("tracing to the session folder (numbers + bar-area crops)")

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        f = getattr(self, "_log_file", None)
        if f is not None and not f.closed:
            f.write(line + "\n")
            f.flush()
        if self.on_log is not None:
            self.on_log(line)
        else:
            print(line, flush=True)

    def stop(self) -> None:
        """Ask the run to end (UI Stop / hotkey). Releases the mouse at once."""
        self.running = False
        self.mouse.release()

    # -- phases ---------------------------------------------------------------------
    def cast(self) -> bool:
        # Catch VFX can look like a reel. Let the old caption clear before the
        # next attempt; a fresh notification then belongs to this fish only.
        if not self._wait_catch_clear():
            return False
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
        if not self.running:
            return False
        self.catch_watch.begin_attempt()
        self.state = "casting"
        self.log(f"cast (hold {self.cfg.cast_hold_s:.2f}s)")
        self.mouse.hold(self.cfg.cast_hold_s)
        return True

    def _sample_catch(self, now: float, image=None, y_off: int = 0) -> None:
        """Centre/lower notification region only, kept in RAM on the OCR worker.

        The saved recording's catch text is near 78% of client height. Use a
        generous band for the two-line passive and different window sizes.
        Reuse a full existing frame, otherwise grab rows at most four times/s.
        """
        if not self.catch_watch.ready(now):
            return
        w, h = self.rect.width, self.rect.height
        ya, yb = int(h * .5), int(h * .91)
        if image is None or y_off > ya or y_off + image.shape[0] < yb:
            if hasattr(self.grabber, "grab_rows"):
                image = self.grabber.grab_rows(ya, yb)
                y_off = ya
            elif image is None:
                image = self.grabber.grab()
                y_off = 0
        lo, hi = max(0, ya - y_off), min(image.shape[0], yb - y_off)
        self.catch_watch.sample(image[lo:hi, int(w * .15):int(w * .85)], now)

    def _poll_catch(self, now: float, active_since=None) -> Optional[CatchNotice]:
        notice = self.catch_watch.poll(now, active_since)
        if self.catch_watch.error and not self._catch_warned:
            self._catch_warned = True
            self.log("catch-text reader unavailable -- using progress for this run "
                     f"({self.catch_watch.error})")
        return notice

    def _wait_catch_clear(self) -> bool:
        self.mouse.release()
        deadline = time.perf_counter() + 8
        waited = False
        while self.running and time.perf_counter() < deadline:
            if not self.focus.ready():
                return False
            now = time.perf_counter()
            self._poll_catch(now)
            if self.catch_watch.error or self.catch_watch.clear_ready:
                return True
            if not waited and now > deadline - 7:
                self.log("waiting for the previous catch notification to clear")
                waited = True
            self._sample_catch(now)
            time.sleep(.02)
        return False

    def _check_catch_after_reel(self, first_seen: float) -> Optional[CatchNotice]:
        # Allow a delayed caption / in-flight OCR result to confirm the catch.
        # Input stays released throughout; the regular reel loop never waits.
        deadline = time.perf_counter() + 1.2
        while self.running and time.perf_counter() < deadline:
            if not self.focus.ready():
                return None
            now = time.perf_counter()
            notice = self._poll_catch(now, first_seen)
            if notice is not None or self.catch_watch.error:
                return notice
            self._sample_catch(now)
            time.sleep(.02)
        return self._poll_catch(time.perf_counter(), first_seen)

    def _finish_reel(self, first_seen: float, last_seen: float,
                     trend: ProgressTrend, notice: Optional[CatchNotice] = None) -> bool:
        self.ctl.step(None, now=time.perf_counter())
        self.mouse.release()
        s = self.ctl.stats
        span = max(0, last_seen - first_seen)
        caught = (True if notice is not None else
                  trend.peak >= CAUGHT_PEAK if trend.last_fill is not None else
                  s.inside_frac > .5 if s.frames else False)
        source = (notice.source if notice is not None else
                  "progress" if trend.last_fill is not None else "tracking estimate")
        rate = f" | {s.frames / span:.0f} readings/s" if span > 0 else ""
        fill = (f" | progress peak {trend.peak:.0%}, last {trend.last_fill:.0%}"
                if trend.last_fill is not None else "")
        result = f" | {'CAUGHT' if caught else 'ESCAPED?'} ({source})"
        self.log("minigame ended (catch confirmed)" if notice is not None else
                 "minigame ended (bar and progress box gone)")
        # Perfect catch (Fisch): the fish never left the bar. The game's progress
        # box says so -- it fills only while the fish is inside, so no sample
        # of it falling. None when the box was not read.
        perfect = (bool(caught) and s.game_in == s.game_n) if s.game_n else None
        what = ""
        if notice is not None and notice.fish:
            what = " ".join([*notice.attributes, notice.mutation or "", notice.fish]).split()
            what = " | " + " ".join(what) + (f" {notice.kg:g}kg" if notice.kg else "")
            if notice.mutation:
                self.mutations[notice.mutation] = self.mutations.get(notice.mutation, 0) + 1
        self.reel_logs.append(s.summary() + rate + fill + result + what
                              + (" | PERFECT" if perfect else ""))
        self.log(self.reel_logs[-1])
        self.reel_history.append({
            "caught": bool(caught), "confirmation": source,
            "peak": round(trend.peak, 3),
            "filling": round(s.game_in / s.game_n, 3) if s.game_n else None,
            "duration": round(span, 1),
            "readings": round(s.frames / span) if span > 0 else 0,
            "input_changes": s.input_changes,
            "perfect": perfect,
            "fish": notice.fish if notice is not None else None,
            "mutation": notice.mutation if notice is not None else None,
            "attributes": list(notice.attributes) if notice is not None else [],
            "kg": notice.kg if notice is not None else None,
        })
        self.live = {"in_reel": False, "fill": None, "inside": None, "elapsed": 0.0}
        self.last_prog_top = None
        self.catch_watch.end_attempt()
        return bool(caught)

    def _trace(self, tag: str, frame: np.ndarray, y_off: int,
               r: Optional[TrackReading], note: str = "") -> None:
        """--trace: one numbers-only line (fischmeasure format) at most every
        TRACE_EVERY_S, about the frame the bot just read (`frame` may be a row
        band starting at client row y_off). Plus small crops of just the bar
        area (_trace_crop) -- never a full screenshot.

        It used to make its own full-screen grab and full bar search every
        0.1s: live at 1920 wide (2026-10-03) that cut the bot to 6-14 readings/s.
        """
        if self.trace_file is None:
            return
        now = time.perf_counter()
        if now - self._trace_last < TRACE_EVERY_S:
            return
        self._trace_last = now
        s = r.scale if r is not None else current_scale()
        if r is not None:
            y0 = r.y0 - y_off
            score = float(np.max(np.convolve(
                edge_scores(frame, max(0, y0 - 2), min(frame.shape[0], r.y1 - y_off + 3), s),
                np.ones(px(GEO_ROWS, s)) / px(GEO_ROWS, s), mode="valid"), initial=0.0))
        else:
            y0, score = fischmeasure.locate_rows(frame, s, y_lo=0 if y_off else None)
        geo = (f"geo=HIT via={r.method} slider={r.slider_x0}-{r.slider_x1} "
               f"fish={r.marker_x:.0f}" if r else "geo=miss")
        p = None
        if self.last_prog_top is not None:
            lo, hi = self._prog_window(self.last_prog_top, s)
            p = find_progress(frame, lo - y_off, hi - y_off, scale=s,
                              polarity=self.progress_polarity,
                              expect_top=self.last_prog_top - y_off)
        if p is None:
            p = find_progress(frame, y0 + px(20, s), y0 + px(100, s), scale=s,
                              polarity=self.progress_polarity,
                              expect_top=y0 + px(GEO_ROWS, s) - 1 + prog_top_dy(s))
        prog = f"prog={p[0]:.3f}@{p[1] + y_off}" if p else "prog=none"
        style = {True: "bright", False: "dark", None: "unknown"}[self.progress_polarity.bright_fill]
        crop = self._trace_crop(frame, y0, s, now, readable=r is not None,
                                bar_seen=score >= EDGE_MIN or p is not None)
        self.trace_file.write(
            f"t={now - self._trace_t0:7.2f} {tag} scale={s:.2f} "
            f"rows={y0 + y_off}-{y0 + y_off + px(GEO_ROWS, s) - 1} "
            f"edge={score:5.0f} {geo} {prog} prog_style={style} held={self.mouse.down} "
            + (f"why=\"{note}\" " if note else "")
            + (f"img={crop} " if crop else "")
            + f"{fischmeasure.sample(frame, y0, s)}\n")
        self.trace_file.flush()

    def _trace_crop(self, frame, y0: int, s: float, now: float, readable: bool,
                    bar_seen: bool) -> Optional[str]:
        """Save a small PNG of just the bar area (track, marker overhang, progress
        box) into the session folder, for working out rods whose reel bar uses a
        different skin. Added 2026-10-03: with Duskwire and Crew Rod the bar and
        progress box were found but never read -- the slider where the white one
        should be measured dark red (45,24,24) -- and the numbers-only trace
        cannot show what the skin looks like.

        Unreadable bars are saved every TRACE_CROP_MISS_S (max TRACE_CROP_MISS_MAX
        per run), readable ones every TRACE_CROP_HIT_S (max TRACE_CROP_HIT_MAX)
        for comparison. Like the rest of the session folder, they are deleted
        when the run stops unless "Keep logs" is on."""
        if not bar_seen:
            return None
        kind = "hit" if readable else "miss"
        every, cap = ((TRACE_CROP_HIT_S, TRACE_CROP_HIT_MAX) if readable
                      else (TRACE_CROP_MISS_S, TRACE_CROP_MISS_MAX))
        if self._crop_n[kind] >= cap or now - self._crop_last[kind] < every:
            return None
        import cv2

        h, w = frame.shape[:2]
        x0, x1 = track_x(w, s)
        rows = px(GEO_ROWS, s)
        ya, yb = max(0, y0 - px(50, s)), min(h, y0 + rows + px(70, s))
        xa, xb = max(0, x0 - px(60, s)), min(w, x1 + px(60, s))
        name = f"bar_{now - self._trace_t0:07.2f}_{kind}.png"
        cv2.imwrite(str(self.session.path(name)),
                    cv2.cvtColor(frame[ya:yb, xa:xb], cv2.COLOR_RGB2BGR))
        self._crop_n[kind] += 1
        self._crop_last[kind] = now
        return name

    @staticmethod
    def _prog_window(top: int, s: float) -> tuple[int, int]:
        """Rows to search for the progress box around where it was last seen."""
        pad = px(PROG_SEARCH_PAD, s)
        return top - pad, top + px(PROG_ROWS, s) + pad

    def _minigame_on_screen(self) -> bool:
        frame = self.grabber.grab()
        if read_track(frame) is not None:
            return True
        top = self.last_prog_top
        return top is not None and find_progress(
            frame, *self._prog_window(top, current_scale())) is not None

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

        Fresh catch text confirms success, even if the passive leaves bar-like
        effects on screen. Otherwise use the progress peak, or the existing
        inside-fraction estimate if progress could not be read.
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
        self.progress_polarity = ProgressPolarity()
        falling_inside_since: Optional[float] = None
        reacquires = 0
        rod_active = False
        self.rod.reset()
        self._slider_widths = []
        last_method = "colour"
        # Non-default reel-bar skins: once the reel is on, a SkinTracker follows
        # the bar instead of the one-frame reader (fischtrack, "Per-reel tracker").
        tracker: Optional[SkinTracker] = None
        tracker_ok = 0.0                 # last time it read the bar
        self.live = {"in_reel": False, "fill": None, "inside": None, "elapsed": 0.0}

        while self.running and time.perf_counter() < deadline:
            if not in_phase and hint is None and time.perf_counter() > bite_deadline:
                self.log(f"no bar within {self.cfg.bite_timeout_s:.0f}s -- "
                         "cast probably failed or fish was lost; recasting")
                self.mouse.release()
                self.catch_watch.end_attempt()
                return False
            if not self.focus.ready():
                self.mouse.release()
                self.log("focus lost mid-reel -- fish almost certainly lost")
                self.catch_watch.end_attempt()
                return False

            self.state = "reeling" if in_phase else "waiting for a bite"
            r, now, img, y_off = self._grab_and_read(hint, tracker)
            notice = self._poll_catch(now, first_seen if in_phase else None)
            if in_phase:
                if notice is not None:
                    return self._finish_reel(first_seen, last_seen, trend, notice)
                self._sample_catch(now, img, y_off)
            if tracker is not None:
                if r is not None:
                    tracker_ok = now
                elif now - tracker_ok > TRACKER_GIVE_UP_S:
                    self.log(f"  lost the bar for {TRACKER_GIVE_UP_S:.1f}s "
                             f"({tracker.why or 'no reading'}) -- re-finding it")
                    tracker = None
                    hint = None
                    self.ctl.reset_motion()
            self._trace("reel" if in_phase else "wait", img, y_off, r,
                        tracker.why if tracker is not None and r is None else "")

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
            scale = r.scale if r is not None else current_scale()
            if r is not None:
                p_lo, p_hi = r.y1 + px(20, scale), r.y1 + px(70, scale)
                p_top = r.y1 + prog_top_dy(scale)
            elif self.last_prog_top is not None and in_phase:
                p_lo, p_hi = self._prog_window(self.last_prog_top, scale)
                p_top = self.last_prog_top
            else:
                p_lo = None
            if p_lo is not None:
                # p_top: where the box must be, for light scenes where its
                # borders do not stand out (fischtrack._box_near)
                p = find_progress(img, p_lo - y_off, p_hi - y_off, scale=scale,
                                  polarity=self.progress_polarity,
                                  expect_top=p_top - y_off)
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
                    elif now - falling_inside_since > (
                            REACQUIRE_TRACKER_S if tracker is not None
                            else REACQUIRE_AFTER_S):
                        reacquires += 1
                        if reacquires <= 3 or self.cfg.debug:
                            self.log(f"  progress falling but bar reading says inside "
                                     f"-- re-acquiring bar (#{reacquires})")
                        hint = None
                        r = None
                        tracker = None
                        self.ctl.reset_motion()
                        falling_inside_since = None
                else:
                    falling_inside_since = None

            if r is not None:
                hint = r
                last_seen = now
                self._slider_widths.append(r.slider_width)
                if r.method != last_method:
                    last_method = r.method
                    if r.method == "geo":
                        self.log("  reading this rod's bar by shape (non-default "
                                 "skin)")
                if in_phase and tracker is None and r.method == "geo":
                    # Re-found mid-reel (tracker gave up or was dropped): the
                    # slider is wherever this reading says, not centred.
                    tracker = SkinTracker(img, r, y_off, now=now, centred=False)
                    tracker_ok = now
                if in_phase:
                    self.learner.feed(img, y_off, r,
                                      p[1] + y_off if p is not None else None)
                if not in_phase:
                    streak += 1
                    if streak == 1:
                        first_seen = now
                    if streak >= self.start_confirm:
                        in_phase = True
                        if locked_scale() is None:
                            # The first reel decides the UI scale for the run.
                            lock_scale(r.scale)
                            self.log(f"UI scale {r.scale:.2f} locked for this run "
                                     f"({self.rect.width}x{self.rect.height} client)")
                        # Timed from the FIRST sighting, not the confirmation:
                        # live reels with boosts last only ~1.5-2s.
                        self.ctl.begin(first_seen)
                        self.log(f"minigame started: track {r.track_x0}-{r.track_x1} "
                                 f"y{r.y0}-{r.y1}, slider {r.slider_width}px")
                        if r.method == "geo":
                            # Every reel starts with slider + fish centred; the
                            # tracker takes it from there.
                            tracker = SkinTracker(img, r, y_off, now=now)
                            tracker_ok = now
                            r = tracker.reading()
                            self.log(f"  following this rod's bar with the skin "
                                     f"tracker (slider {tracker.w}px)")
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

            self.mouse.release()
            notice = self._check_catch_after_reel(first_seen)
            return self._finish_reel(first_seen, last_seen, trend, notice)

        self.mouse.release()
        # Without a minigame the stats are still the previous cast's (live log,
        # 2026-10-03: "reel timed out: ... filling 246/246 (100%)" after a cast
        # made with no rod in hand) -- say what happened instead.
        self.log("reel timed out: " + (self.ctl.stats.summary() if in_phase
                                       else "no minigame appeared (no bite, or no rod in hand?)"))
        self.catch_watch.end_attempt()
        return False

    def _grab_and_read(self, hint: Optional[TrackReading],
                       tracker: Optional[SkinTracker] = None):
        """Grab + read one frame. With a hint, grab only the rows around the bar;
        with a tracker (non-default skin, reel on), let it read them.

        A full 1920x1009 grab + search measured ~65ms/frame live, too slow for the
        servo (sim_servo.py degrades past ~40ms). The band is ~4x fewer rows, and
        read_track's hint mode never looks outside it.

        Returns (reading, time, image, image_y_offset) -- the image is passed on
        so the progress box is read from the same grab.
        """
        h = self.grabber.rect.height if self.grabber.rect is not None else None
        if tracker is not None:
            # Same band (it also covers the progress box), read by the tracker.
            if h is None or not hasattr(self.grabber, "grab_rows"):
                frame = self.grabber.grab()
                now = time.perf_counter()
                return tracker.read(frame, 0, now=now), now, frame, 0
            pad = px(HINT_Y_PAD + TRACK_PROBE + 20, tracker.s)
            y_a = max(0, tracker.y0 - pad)
            band = self.grabber.grab_rows(y_a, min(h, tracker.y1 + pad))
            now = time.perf_counter()
            return tracker.read(band, y_a, now=now), now, band, y_a
        exp = self._expect_w()
        if hint is None or h is None or not hasattr(self.grabber, "grab_rows"):
            frame = self.grabber.grab()
            return (read_track(frame, hint=hint, expect_w=exp), time.perf_counter(),
                    frame, 0)
        pad = px(HINT_Y_PAD + TRACK_PROBE + 20, hint.scale)   # covers the progress box too
        y_a = max(0, hint.y0 - pad)
        y_b = min(h, hint.y1 + pad)
        band = self.grabber.grab_rows(y_a, y_b)
        now = time.perf_counter()
        local = replace(hint, y0=hint.y0 - y_a, y1=hint.y1 - y_a)
        r = read_track(band, hint=local, expect_w=exp)
        if r is not None:
            r = replace(r, y0=r.y0 + y_a, y1=r.y1 + y_a)
        return r, now, band, y_a

    def _expect_w(self) -> Optional[float]:
        """Median slider width of this reel's recent readings, once there are a
        few. Steadies the colour-agnostic slider reading (fischtrack
        find_slider_geo): mid-reel the width only changes with a boost."""
        w = self._slider_widths[-SLIDER_W_WINDOW:]
        return float(np.median(w)) if len(w) >= 3 else None

    def _check_quests(self) -> None:
        """Read the quest tracker (left of the screen) and log what changed;
        on the first read, the tracked quests and a plan for objectives that
        need a mutation. Never fails the run: a reading error turns it off."""
        if not self.track_quests:
            return
        from fischquest import find_open_chat, plan, read_tracker

        try:
            frame = self.grabber.grab()
            # An open Roblox chat can cover the tracker: close it (its topbar
            # button is a solid bubble while open). Between casts only.
            chat = find_open_chat(frame)
            if chat is not None and self.focus.ready():
                from fischequip import WinInput
                WinInput().click(self.rect.left + chat[0], self.rect.top + chat[1])
                user32.SetCursorPos(self.rect.left + self.rect.width // 2,
                                    self.rect.top + int(self.rect.height * 0.55))
                time.sleep(0.35)
                frame = self.grabber.grab()
                self.log("quests: Roblox chat was open over the quest tracker -- closed it"
                         + ("" if find_open_chat(frame) is None else " (still open?)"))
            quests = read_tracker(frame)
        except Exception as exc:
            self.log(f"quest tracker unreadable ({exc!r}) -- quest tracking off for this run")
            self.track_quests = False
            return
        state = {(q.title, o.text): (o.have, o.need, o.done)
                 for q in quests for o in q.objectives}
        first = self._quest_state is None
        if first:
            if not quests:
                self.log("quests: no quest tracker on screen (track quests in the Quest "
                         "Book to have the bot follow them)")
            else:
                self.log(f"quests: {len(quests)} tracked, "
                         f"{sum(1 for q in quests if q.done)} complete")
                for q in quests:
                    if q.done:
                        continue
                    left = [o for o in q.objectives if not o.done]
                    self.log(f"  {q.title}" + (f" ({q.npc}, {q.location})" if q.npc else "")
                             + ": " + "; ".join(
                                 o.text + (f" {o.have}/{o.need}" if o.need else "")
                                 for o in left))
                for line in plan(quests, self.owned_rods):
                    self.log("  plan: " + line)
        else:
            for key, (have, need, done) in state.items():
                old = self._quest_state.get(key)
                if old is None or old == (have, need, done):
                    continue
                title, text = key
                self.log(f"quest progress: {title}: {text} "
                         f"{old[0]}/{old[1]} -> {have}/{need}" + (" -- DONE" if done else ""))
            was_done = {q.title for q in self.quests if q.done}
            for q in quests:
                if q.done and q.title not in was_done and any(
                        k[0] == q.title for k in self._quest_state):
                    self.log(f"QUEST COMPLETE: {q.title}"
                             + (f" -- hand it in to {q.npc} ({q.location})" if q.npc else ""))
        self.quests, self._quest_state = quests, state
        from fischquest import quests_view
        self.quest_view = dict(quests_view(quests, self.owned_rods), read_at=time.time())

    def equip_rod(self, name: str) -> bool:
        """Equip `name` in the game through the Equipment Bag (fischequip.py).
        Worker thread, between casts only: the rod is reeled in after a reel, and
        N does not open while it is cast. The menu is always closed again.
        False (and logged) if it could not be done."""
        from fischequip import VK_T, EquipmentMenu, MenuError, WinInput

        if not self.focus.ready():
            self.log(f"equip {name}: Roblox is not in front -- skipped")
            return False
        self.mouse.release()
        prev_state, self.state = self.state, "equipping rod"
        self.log(f"equipping {name} (Equipment Bag)")
        try:
            with EquipmentMenu(self.grabber.grab, self.rect, self.log,
                               cancelled=lambda: not self.running) as menu:
                result = menu.equip(name)
            self.log(f"  {name}: " + ("already equipped in the bag"
                                      if result == "already" else "equipped in the bag"))
            # The bag only puts it in the hotbar; it must still be taken in hand
            # (live 2026-10-03: the cast after a switch did nothing). Fisch's T
            # key takes the equipped rod out -- the user's find; it replaced
            # reading the hotbar for the rod's slot. Like a hotbar key it would
            # put AWAY a rod already in hand, so skip it when this run last took
            # exactly this rod out.
            if result == "equipped" or self._held_rod != name:
                time.sleep(0.3)                  # the bag's close animation
                WinInput().tap(VK_T)
                self._held_rod = name
                self.log(f"  {name}: taken in hand (T)")
            return True
        except MenuError as exc:
            self.log(f"  could not equip {name}: {exc}")
            return False
        except Exception as exc:          # OCR missing etc.: never kill the run
            self.log(f"  could not equip {name}: {exc!r}")
            return False
        finally:
            self.state = prev_state
            self.recentre()
            time.sleep(0.2)

    def recentre(self) -> None:
        """Cursor back over the game's middle, where casting clicks belong."""
        if not self.mouse.dry_run:
            user32.SetCursorPos(self.rect.left + self.rect.width // 2,
                                self.rect.top + int(self.rect.height * 0.55))

    def _use_items(self) -> None:
        """Useables tab (fischuse.py): totems and baits, between casts only.
        Never lets a problem there end the run."""
        u = self.useables
        if u is None or not u.active or not self.running:
            return
        if not self.focus.ready():
            return
        self.mouse.release()
        prev, self.state = self.state, "using items"
        try:
            u.between_casts(self)
        except Exception as exc:
            self.log(f"useables: skipped this time ({exc!r})")
        finally:
            self.state = prev

    # -- main -----------------------------------------------------------------------
    def apply_configuration(self, cfg: MacroConfig, ccfg: ControlConfig,
                            rod: RodProfile, enchants: list[str], keep: bool) -> None:
        """Worker-thread only, between casts; never mutate an active reel."""
        self.mouse.release()
        if cfg.trace and self.trace_file is None:
            self.trace_file = self.session.path(
                f"trace_{datetime.fromtimestamp(time.time()):%Y%m%d_%H%M%S}.txt").open('a', encoding='utf-8')
        elif not cfg.trace and self.trace_file is not None:
            self.trace_file.close()
            self.trace_file = None
        self.cfg, self.ccfg, self.ctl.cfg = cfg, ccfg, ccfg
        self.focus.mode = cfg.focus_mode
        self.rod, self.enchants = rod, list(enchants)
        self.catch_watch.rod = rod.name
        self.session.keep_logs = keep
        self.last_prog_top = None
        self.pending_hint = None
        self.ctl.reset_motion()

    def run(self) -> None:
        self.running = True
        self.mouse.place_cursor(self.rect)
        if self.cfg.diagnose:
            return self.diagnose_loop()

        # Every run assumes a new area: reset geometry, check the window and the
        # idle scene, and re-learn the bar's position on the first reel.
        self.state = "calibrating"
        pre = precheck(self.grabber.grab, self.rect.width, self.rect.height, self.log)
        self._check_quests()
        self._use_items()
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
                if self.on_cycle_boundary is not None:
                    self.on_cycle_boundary()
                if self.cfg.max_fish and self.cycles >= self.cfg.max_fish:
                    self.log(f"reached --max-fish={self.cfg.max_fish}")
                    break
                if not self.focus.ready():
                    time.sleep(0.4)
                    continue

                if not self.cast():
                    continue
                time.sleep(self.cfg.cast_pause_s)
                self.lure()
                if self.reel():
                    self.caught += 1
                else:
                    self.lost += 1
                self.cycles += 1
                self.log(f"cycle {self.cycles} done: {self.caught} caught, "
                         f"{self.lost} lost")
                if self.useables is not None:
                    self.useables.cast_done()
                self._check_quests()
                self._use_items()
                # Caption-clear detection already guards the next cast. Keep
                # only a short breather instead of adding another 0.6s delay.
                time.sleep(0.15)
        finally:
            self.catch_watch.close()
            self.state = "stopped"
            self.mouse.release()
            self.mouse.restore_cursor()
            if self.trace_file is not None:
                self.trace_file.close()
            self.log(f"stopped -- {self.cycles} cycles, {self.caught} caught, "
                     f"{self.lost} lost")
            perfect = sum(1 for h in self.reel_history if h.get("perfect"))
            if self.reel_history:
                self.log(f"perfect catches: {perfect}/{sum(1 for h in self.reel_history if h['caught'])}")
            if self.mutations:
                self.log("mutations caught: " + ", ".join(
                    f"{m} x{n}" for m, n in sorted(self.mutations.items(), key=lambda kv: -kv[1])))
            if self.reel_logs:
                self.session.path("reel_runs.log").write_text(
                    "\n".join(self.reel_logs), encoding="utf-8")
            self.close_log()
            if self._own_session:
                self.session.close()

    def close_log(self) -> None:
        """Close bot.log (Windows cannot delete the session folder around an
        open file). Later log() lines still reach the screen."""
        f = getattr(self, "_log_file", None)
        if f is not None and not f.closed:
            f.close()

    def diagnose_loop(self) -> None:
        """Watch only, send nothing.

        Prints every fischtrack reading (track, slider, fish marker) plus the
        frame size and timing, in CLIENT coordinates -- which is what the live
        macro sees, unlike the full-desktop recording. Do a full cast and reel by
        hand, then Ctrl+C. Optionally saves annotated frames with --debug.
        """
        self.log("DIAGNOSE: watching only, no input sent. Do a cast + reel.")
        set_client(self.rect.width, self.rect.height)
        self.log(f"client {self.rect.width}x{self.rect.height}: trying UI scales "
                 f"{', '.join(f'{s:.2f}' for s in scales())}")
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
                          f"{r.slider_x0}-{r.slider_x1} (w={r.slider_width}) "
                          f"scale {r.scale:.2f} fish "
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
                    help="log bar/progress measurements ~10x/s, plus small crops of "
                         "the bar area when it can't be read, to the session folder, "
                         "for diagnosing new spots and rods")
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
