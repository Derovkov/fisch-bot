"""
Reel controller: a bang-bang position servo, plus the timing rules that bracket it.

Why a controller and not a click macro
--------------------------------------
Per the wiki, holding the input accelerates the control slider RIGHT and releasing
accelerates it LEFT, and the fish moves on its own. Success is keeping the fish
inside the slider. That makes this a closed-loop position problem: the correct
input depends on the fish's position *now*, so no fixed click cadence can win it.
Every earlier attempt in this folder was a timing macro and could not.

Reference performance, from the wiki's own demonstration GIF (198 frames @40ms):
the human playing it kept the fish inside the slider for 177/198 frames (89%) with
a median tracking error of 20px against a 189px-wide slider. That is the bar to
beat, and it is a deliberately hard one.

Game rules that shape the loop, all from the wiki:
  * the minigame opens LOCKED: input is ignored for 1.2s, or until progress hits
    20%. Holding the button through the lock wastes nothing but does nothing.
  * progress moves +-12% per second depending on whether the fish is inside.
  * a perfect catch (never losing progress) pays 10 C$ and +50% XP, so tracking
    cleanly is worth real money, not just a catch.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional



@dataclass
class ControlConfig:
    """Tunables for the servo loop."""

    # Deadband as a fraction of slider width. 0.06 of 189px = ~11px. Small enough
    # to be accurate, large enough not to chatter: the slider accelerates the
    # instant input flips, so reacting to a 1px error guarantees oscillation.
    deadband_frac: float = 0.06

    # Seconds after the bar appears during which no input is sent. The wiki's lock
    # is 1.2s OR until progress hits 20%, and the game simply ignores input
    # during it -- so steering through it is harmless. Live reels with progress
    # boosts lasted only ~1.5-2s (2026-10-02), and a 1.2s wait left the
    # controller no time at all. Hence 0.
    lock_s: float = 0.0

    # Abandon the fish if the slider has been off-screen this long, which means the
    # minigame ended (caught or lost) rather than the detector failing.
    lose_timeout_s: float = 2.0

    # Give up after this long even if the bar is still visible, so a stuck frame
    # cannot hold the button down forever.
    max_reel_s: float = 45.0

    # Hold duration floor. A hold shorter than one frame is not a real input change,
    # so sub-frame toggles are collapsed into "keep current state".
    # Raised 0.02 -> 0.05 once the reader got fast enough (~7ms) to outrun the
    # screen: flips faster than ~3 frames are noise, not control.
    min_hold_s: float = 0.05

    # Predictive term. The slider has inertia (measured on the recording: accel
    # ~1000-1500 px/s^2, speeds up to ~450 px/s), so steering on position error
    # alone overshoots. The controller steers on the error PREDICTED this far
    # ahead using velocity estimates: u = err + lookahead*(v_fish - v_slider).
    # 0 reproduces the old pure-position law. Tuned in sim_servo.py (assumed
    # physics, swept over accel 800-3000 px/s^2): 0 keeps the fish inside only
    # 51-62% of the time; 0.5 keeps it inside 100% at 40ms loop latency and
    # >=95% at 80ms. Latency is the thing that kills it -- keep the loop fast.
    lookahead_s: float = 0.50

    # EMA factor for the velocity estimates (per frame). Detector x is quantised
    # to 1px, so raw frame-to-frame differences at 60fps are noisy.
    vel_alpha: float = 0.35

    # Min time between the samples a velocity is computed from (~2 screen frames).
    vel_min_dt_s: float = 0.03


@dataclass
class ReelStats:
    frames: int = 0
    inside: int = 0
    unseen: int = 0
    flips: int = 0
    input_changes: int = 0
    max_err: float = 0.0
    err_sum: float = 0.0
    err_n: int = 0
    lost: bool = False
    caught: bool = False
    # The game's verdict from the progress box (fischbot.ProgressTrend): frames
    # where it was filling vs draining. Unlike `inside`, this does not depend on
    # the bar reading being right -- and it counts frames the bar was not read.
    game_in: int = 0
    game_out: int = 0

    @property
    def game_n(self) -> int:
        return self.game_in + self.game_out

    def note_game(self, verdict: Optional[bool]) -> None:
        if verdict is True:
            self.game_in += 1
        elif verdict is False:
            self.game_out += 1

    @property
    def inside_frac(self) -> float:
        return self.inside / self.frames if self.frames else 0.0

    @property
    def mean_err(self) -> float:
        return self.err_sum / self.err_n if self.err_n else 0.0

    def summary(self) -> str:
        game = (f"GAME: filling {self.game_in}/{self.game_n} "
                f"({self.game_in / self.game_n * 100:.0f}%) | " if self.game_n
                else "GAME: no progress verdict | ")
        return (
            game +
            f"{self.frames} frames | bar reading inside {self.inside}/{self.frames} "
            f"({self.inside_frac*100:.0f}%) | mean|err| {self.mean_err:.0f}px | "
            f"max {self.max_err:.0f}px | fish unseen {self.unseen} | "
            f"input changes {self.input_changes}"
        )


class ReelController:
    """Turns ReelState readings into hold/release decisions, and paces the input.

    `held` is the controller's memory of the current input state. decide() returns
    None to mean "no change", which is different from "release" -- conflating those
    two is how a naive implementation ends up chattering.
    """

    def __init__(self, cfg: ControlConfig | None = None):
        self.cfg = cfg or ControlConfig()
        self.held = False
        self._last_change = 0.0
        self.stats = ReelStats()

    def reset(self) -> None:
        self.held = False
        self._last_change = 0.0
        self.stats = ReelStats()
        self._prev: Optional[tuple[float, float, float]] = None  # t, slider, fish
        self.v_slider = 0.0
        self.v_fish = 0.0

    def _update_velocity(self, t: float, slider_c: float, fish_x: float) -> None:
        if self._prev is not None:
            dt = t - self._prev[0]
            if dt < self.cfg.vel_min_dt_s:
                # The loop can outrun the screen (~60Hz): differencing a frame
                # against its own duplicate gives v=0 then a spike, which made
                # the input chatter. Wait for a sample far enough apart.
                return
            if dt < 0.25:
                a = self.cfg.vel_alpha
                self.v_slider += a * ((slider_c - self._prev[1]) / dt - self.v_slider)
                self.v_fish += a * ((fish_x - self._prev[2]) / dt - self.v_fish)
            elif dt >= 0.25:
                self.v_slider = self.v_fish = 0.0   # stale: start over
        self._prev = (t, slider_c, fish_x)

    def decide(self, state) -> Optional[bool]:
        """Predictive bang-bang. True = hold (accelerate right), False = release."""
        err = state.error
        if err is None:
            return None
        u = err + self.cfg.lookahead_s * (self.v_fish - self.v_slider)
        band = state.slider_w * self.cfg.deadband_frac
        if u > band:
            return True
        if u < -band:
            return False
        return None

    def begin(self, now: float | None = None) -> float:
        """Call at minigame start. Returns the time input is unlocked."""
        self.reset()
        t = now if now is not None else time.perf_counter()
        self._start = t
        return t + self.cfg.lock_s

    def step(self, state, now: float | None = None,
             deadline: float | None = None) -> Optional[bool]:
        """Process one frame.

        Returns the input state to HOLD (True) or RELEASE (False), or None when the
        caller should change nothing this frame. Returns False additionally when the
        minigame appears to have ended.

        `state` is a fischtrack.TrackReading (slider + fish marker). Anything
        without a `fish_x` (e.g. the old slider-only SliderReading) is observed
        but never acted on.

        Pass None only once the minigame is believed OVER: it releases at once.
        A single missed frame (VFX flash over the marker) should not be passed
        here -- the caller should skip the call and keep the current input.
        """
        t = now if now is not None else time.perf_counter()
        if state is None:
            self.stats.unseen += 1
            # The bar vanishing means the fish was landed or escaped. Release
            # before anything else so a stale hold cannot leak into the next cast.
            if self.held:
                self.held = False
                self._last_change = t
                self.stats.input_changes += 1
                return False
            if deadline is not None and t > deadline:
                self.stats.lost = True
                return False
            return None

        self.stats.frames += 1

        # A SliderReading carries geometry only -- no fish marker. Record the
        # observation and stop there. Acting without a target would just fling the
        # slider at a wall and lose the fish.
        if not hasattr(state, "fish_x"):
            return None

        if state.fish_x is not None:
            err = state.error
            if err is not None:
                a = abs(err)
                self.stats.err_sum += a
                self.stats.err_n += 1
                self.stats.max_err = max(self.stats.max_err, a)
        if state.fish_inside:
            self.stats.inside += 1

        if state.fish_x is not None and state.slider_w:
            self._update_velocity(t, state.slider_centre, state.fish_x)

        if t < getattr(self, "_start", 0.0) + self.cfg.lock_s:
            return None  # locked; input is ignored by the game

        want = self.decide(state)
        if want is None:
            return None  # inside the deadband, or fish unseen: hold current input

        if want == self.held:
            return None
        if t - self._last_change < self.cfg.min_hold_s:
            return None  # too soon since the last flip

        self.held = want
        self._last_change = t
        self.stats.input_changes += 1
        return want

    def timed_out(self, now: float | None = None) -> bool:
        t = now if now is not None else time.perf_counter()
        return t > getattr(self, "_start", 0.0) + self.cfg.max_reel_s