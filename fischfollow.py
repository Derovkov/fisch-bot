"""Follow a skinned reel bar by colour columns plus a motion model.

Why (2026-10-05): the shape readers (fischtrack: read_track "geo",
SkinTracker) look for a slider of one colour between two edges. Fabulous Rod
and Noiseform bars broke that: a see-through track over a picture, a black
slider with arrows that come and go and a shine across it, a pill-shaped fish
crossed by the fishing line, a slider that grows mid-reel (270 -> 410px on the
recording) and a purple boost glow spilling over the track. Live they read
15-18 times a second and drifted 100-200px off.

BarFollower sits on top of the SkinTracker for one reel. Each frame it looks at
the track as a row of columns (each column's median colour over the track rows):

  * the slider's colour -- the main colour of the slider at reel start (it is
    centred there, its span known); its shine, arrows and the fish are minor;
  * the track's colour, per column -- learned from every column the slider is
    not over (the picture behind a see-through track never moves); columns not
    seen uncovered yet are interpolated from their neighbours;
  * each column scores "looks like the slider, not like the track there"; the
    slider is the stretch of columns with the best total (maximum subarray), so
    a shine, an arrow or the fish inside it only cost a little, its width can
    change, and a glow beside it scores nothing against the learned track;
  * the columns where the slider is expected (motion model) get a small bonus,
    which carries it across its shine before the track behind it is learned;
  * motion -- the slider speeds up one way while the mouse is held and the
    other way when released, the same for every skin. A small model (position,
    velocity, one acceleration per mouse state, learned during the reel)
    predicts where it is, rejects readings that jump somewhere impossible and
    bridges frames where nothing could be read (up to COAST_S).

The SkinTracker's own slider reading is used only when the columns give none.
The fish comes from the SkinTracker's reading; when that failed (usually the
slider part), from its fish finder alone (`find_fish`); else it is coasted.
Tune against recordings: dev_tests/replay_skin.py.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from fischtrack import TrackReading, px

LIKE_CLIP = (-60.0, 150.0)  # per-column score range (channel-sum distances)
LIKE_BIAS = 15.0        # a column must beat this to count towards the slider
PRIOR_BONUS = 45.0      # added where the slider is expected
SCORE_MIN = 400.0       # best stretch's total needed to count as a reading
WIDTH_RANGE = (0.5, 2.2)  # x the reel-start width (boosts grow it)
BG_LEARN = 0.2          # track colour per column: weight of a new uncovered look
BG_MARGIN = 6           # px either side of the slider not learned as track
ALPHA, BETA = 0.6, 0.35  # how far a measurement pulls position / velocity
WIDTH_LEARN = 0.5
ACC_LEARN = 0.15        # acceleration estimate: weight of each new sample
SLIDER_SPEED = 1.4      # track widths / s searched around the prediction (fischtrack: 1.2)
COAST_S = 0.5           # longest stretch without a slider reading before giving up
FISH_COAST_S = 1.5       # the fish moves slowly: an older position still steers right


def _rows(r: TrackReading) -> tuple[int, int]:
    """Track rows inside the rim (absolute)."""
    return r.y0 + px(6, r.scale), r.y1 - px(5, r.scale) + 1


def best_stretch(L: np.ndarray) -> tuple[int, int, float]:
    """[l, r) with the largest sum of L (Kadane), and that sum."""
    best, cur, start, l, r = -np.inf, 0.0, 0, 0, 0
    for x, v in enumerate(L):
        if cur <= 0:
            cur, start = v, x
        else:
            cur += v
        if cur > best:
            best, l, r = cur, start, x + 1
    return l, r, float(best)


class SliderModel:
    """Position + velocity of the slider centre (px from the track start), with
    one learned acceleration while held and one while released."""

    def __init__(self, c: float, lo: float, hi: float):
        self.c, self.v, self.lo, self.hi = c, 0.0, lo, hi
        self.acc = {True: 0.0, False: 0.0}

    def predict(self, dt: float, held: bool) -> tuple[float, float]:
        a = self.acc[held]
        c = self.c + self.v * dt + 0.5 * a * dt * dt
        v = self.v + a * dt
        if c <= self.lo or c >= self.hi:            # the track ends stop it
            c, v = min(max(c, self.lo), self.hi), 0.0
        return c, v

    def correct(self, pred: tuple[float, float], meas: float, dt: float, held: bool) -> None:
        c, v = pred
        err = meas - c
        new_v = v + BETA * err / max(dt, 1e-3)
        if dt > 1e-3 and self.lo < meas < self.hi:
            self.acc[held] += ACC_LEARN * ((new_v - self.v) / dt - self.acc[held])
        self.c, self.v = c + ALPHA * err, new_v

    def coast(self, pred: tuple[float, float]) -> None:
        self.c, self.v = pred


class BarFollower:
    def __init__(self, frame: np.ndarray, r: TrackReading, y_off: int = 0, now: float = 0.0,
                 find_fish=None):
        """`find_fish(frame, y_off, near, reach)` -> fish x in track px, or None
        (SkinTracker._find_marker)."""
        self.r0 = r
        self.find_fish = find_fish
        self.x0, self.x1 = r.track_x0, r.track_x1
        self.W = self.x1 - self.x0 + 1
        a, b = r.slider_x0 - self.x0, r.slider_x1 - self.x0 + 1
        self.w0 = self.w = float(b - a)
        self.ya, self.yb = _rows(r)
        self.fish = (r.marker_x - self.x0) if r.marker_x is not None else (a + b) / 2
        P = self._profile(frame, y_off)
        self.cs = self._main_colour(P[a:b], self.fish - a)
        self.bg = P.copy()
        self.known = np.ones(self.W, bool)
        self.known[max(0, a - BG_MARGIN):b + BG_MARGIN] = False
        self.model = SliderModel((a + b) / 2, self.w / 2, self.W - self.w / 2)
        self.t = self.t_slider = self.t_fish = now
        self.why = ""
        self.score = 0.0

    # -- colours ----------------------------------------------------------------------
    def _profile(self, frame: np.ndarray, y_off: int) -> np.ndarray:
        band = frame[self.ya - y_off:self.yb - y_off, self.x0:self.x1 + 1]
        return np.median(band.astype(np.float32), axis=0)

    def _main_colour(self, cols: np.ndarray, fish: float) -> np.ndarray:
        """The slider's main colour: the biggest of 3 colour groups among its
        columns (its shine, arrows and the fish are the smaller ones)."""
        keep = np.abs(np.arange(len(cols)) - fish) > px(12, self.r0.scale)
        pts = np.ascontiguousarray(cols[keep] if keep.sum() >= 6 else cols, np.float32)
        if len(pts) < 6:
            return np.median(pts, axis=0)
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, lab, centres = cv2.kmeans(pts, 3, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        return centres[np.argmax(np.bincount(lab.ravel(), minlength=3))]

    def _track_colour(self) -> np.ndarray:
        """The learned track colour per column; unseen columns interpolated."""
        if self.known.all() or not self.known.any():
            return self.bg
        xs = np.arange(self.W)
        k = xs[self.known]
        return np.stack([np.interp(xs, k, self.bg[self.known, ch]) for ch in range(3)], 1)

    def _measure(self, P: np.ndarray, c_pred: float) -> Optional[tuple[int, int, float]]:
        d_track = np.abs(P - self._track_colour()).sum(1)
        d_slider = np.abs(P - self.cs).sum(1)
        L = np.clip(d_track - d_slider, *LIKE_CLIP) - LIKE_BIAS
        a, b = int(c_pred - self.w / 2), int(c_pred + self.w / 2)
        L[max(0, a):max(0, b)] += PRIOR_BONUS
        l, r, score = best_stretch(L)
        self.score = score
        lo, hi = WIDTH_RANGE
        if score < SCORE_MIN or not lo * self.w0 <= r - l <= hi * self.w0:
            return None
        return l, r, score

    # -- per frame ------------------------------------------------------------------
    def update(self, frame: np.ndarray, y_off: int, now: float, held: bool,
               r: Optional[TrackReading]) -> Optional[TrackReading]:
        dt = max(1e-3, now - self.t)
        self.t = now
        pred = self.model.predict(dt, held)
        reach = SLIDER_SPEED * self.W * dt + px(20, self.r0.scale)
        P = self._profile(frame, y_off)
        m = self._measure(P, pred[0])
        meas, width = None, self.w
        if m is not None:
            meas, width = (m[0] + m[1]) / 2, float(m[1] - m[0])
        elif r is not None and r.slider_x0 is not None:
            meas = r.slider_centre - self.x0
        if meas is not None and abs(meas - pred[0]) > reach + abs(width - self.w) / 2 \
                and now - self.t_slider < COAST_S:
            meas = None                                  # an impossible jump: ignore it
        if meas is not None:
            self.model.correct(pred, meas, dt, held)
            self.w += WIDTH_LEARN * (width - self.w)
            self.model.lo, self.model.hi = self.w / 2, self.W - self.w / 2
            self.t_slider = now
            if m is not None:
                # everything clear of the slider (and the fish) is track
                clear = np.ones(self.W, bool)
                clear[max(0, m[0] - BG_MARGIN):m[1] + BG_MARGIN] = False
                f = int(self.fish)
                clear[max(0, f - px(12, self.r0.scale)):f + px(12, self.r0.scale)] = False
                upd = clear & self.known
                self.bg[upd] += BG_LEARN * (P[upd] - self.bg[upd])
                new = clear & ~self.known
                self.bg[new] = P[new]
                self.known |= clear
        else:
            self.model.coast(pred)
            if now - self.t_slider > COAST_S:
                self.why = f"slider not seen for {COAST_S:.1f}s (column score {self.score:.0f})"
                return None
        f = r.marker_x - self.x0 if r is not None and r.marker_x is not None else None
        if f is None and self.find_fish is not None:
            f = self.find_fish(frame, y_off, self.fish, max(px(40, self.r0.scale), 0.8 * self.W * dt))
        if f is not None:
            self.fish, self.t_fish = f, now
        elif now - self.t_fish > FISH_COAST_S:
            self.why = f"fish not seen for {FISH_COAST_S:.1f}s"
            return None
        w = int(round(self.w))
        a = int(round(self.model.c - self.w / 2))
        return TrackReading(self.x0, self.x1, self.r0.y0, self.r0.y1, self.x0 + a,
                            self.x0 + a + w - 1, self.x0 + self.fish,
                            scale=self.r0.scale, method="follow")
