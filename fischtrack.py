"""
Reel-minigame reader: TRACK, SLIDER and FISH MARKER from one frame.

Measured on the 1920x1080 recording (2026-10-01 18-56-45.mp4):

  track   translucent dark bar, x~572-1348 (777px), 31px tall. ~(55,41,62) over the
          dark floor, ~(100,75,105) over a brighter one -- its absolute colour
          depends on what is behind it, so it is found by being DARKER THAN THE
          ROWS ABOVE AND BELOW it, not by a fixed threshold. Screen-space UI: it
          does not follow the camera. It slides up from the bottom edge when the
          minigame opens (y~1030 -> 869 over ~40 frames) and back down when it
          closes.
  slider  pale bar inside the track, ~(250,225,246) shading to (228,245,249);
          during a progress boost it widens (252 -> ~405px) and goes pinker
          ~(210,170,244). Rod VFX (green spikes) draw over it.
  marker  thin vertical capsule ~(228,186,208), ~12px wide, ~60px tall: it sticks
          out ~15px above and below the track, with a pale fish icon above it.
          LIGHT, and pinker / less blue than the slider. The old "slider colour"
          constant (228,187,211) in fischslider.py is actually this marker.

The track's presence is also the phase signal. On the recording it is on screen
for frames 158-534 and 610-923. HUD absence covers only part of that: the HUD is
back for frames ~516-534 of reel 1 and for most of reel 2 (612-792).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Rows to scan, as a fraction of frame height. Generous on purpose: the bar slides
# in from the bottom edge.
SEARCH_TOP_FRAC = 0.70
SEARCH_BOTTOM_FRAC = 1.00
# ...or at least this many px (at UI scale) above the bottom edge, whichever is
# higher. With fixed-size UI on a short window the bar sits higher than 0.70 of
# the height. Measured at 1920x1009: track bottom row 132px above the bottom edge.
SEARCH_MIN_PX = 260

# Track pixel: no channel above TRACK_MAX, and channel-sum at least TRACK_CONTRAST
# below the mean of the pixels TRACK_PROBE rows above and below (the track is 31px
# tall, so both probes land outside it).
TRACK_MAX = 140
TRACK_CONTRAST = 50
TRACK_PROBE = 34

# Slider pixel: bright and blue-leaning. Excludes the green rod VFX.
SLIDER_MIN = 150
SLIDER_B_MIN = 200
SLIDER_MIN_WIDTH_FRAC = 0.20    # 252/777 = 0.32 normally; wider during a boost
SLIDER_MAX_WIDTH_FRAC = 0.62    # widest boost measured 431/777 = 0.55

# A bar row: one run of (track | slider) pixels, gaps <= BAR_MERGE bridged (VFX),
# spanning this fraction of frame width (777/1920 = 0.405).
BAR_MERGE = 40
TRACK_GAP = 50                  # max gap between solid runs / slider edge (VFX,
                                # boosted slider's soft edge measured at 39px)
BAR_MIN_SPAN_FRAC = 0.36
BAR_MAX_SPAN_FRAC = 0.45
BAR_MIN_ROWS = 6
BAR_MAX_ROWS = 44               # track is 31px; the 48px Windows taskbar is not a bar

# Tracking mode (read_track with hint=previous reading).
HINT_Y_PAD = 60                 # bar slides ~4px/frame at most during its entrance
HINT_X_PAD = 40
HINT_X_TOL = 30                 # track ends measured 549-572 / 1347-1373 on the recording

# Marker pixel: pink-white, less blue than the slider in every state measured.
MARKER_R = (195, 250)
MARKER_G = (160, 205)
MARKER_B = (180, 228)
MARKER_RG_MIN = 22
MARKER_MIN_HEIGHT = 20          # px of marker-coloured pixels in one column
MARKER_MAX_WIDTH = 24


# ======================================================================================
# UI scale (2026-10-03)
# ======================================================================================
#
# Every pixel size in this file was measured on a 1920x1009 client (1920 wide,
# maximised, with the taskbar showing). That is UI scale 1.0. On another client
# size the bar is drawn bigger or smaller, and how much depends on how Fisch
# sizes its UI, which has not been measured yet. The three usual Roblox rules
# each give a candidate:
#
#   * sized relative to the screen WIDTH          -> scale = w / 1920
#   * sized relative to the screen HEIGHT         -> scale = h / 1009
#   * fixed pixel sizes                           -> scale = 1.0
#
# Until the first reel the reader tries every candidate and keeps the reading
# whose track ends have the strongest edges. Candidates can be close (0.667 vs
# 0.643 at 1280x649), and the wrong one still "reads" the bar with its ends ~10px
# off, so the first reading found is not good enough. The first reel's scale is
# then locked for the rest of the run (lock_scale), and fischcalib.GeometryLearner
# measures the real ends. At 1920x1009 all three are 1.0, so nothing changes
# there.

REF_WIDTH = 1920
REF_HEIGHT = 1009

_scales: list[float] = [1.0]        # candidates for the current client
_locked: Optional[float] = None     # the one the first reel was read at


def px(n: float, s: float) -> int:
    """A pixel size measured at scale 1.0, at scale s (at least 1px)."""
    return max(1, int(n * s + 0.5))


def scale_candidates(w: int, h: int) -> list[float]:
    """UI scales to try for a w x h client, most likely first, duplicates dropped."""
    out: list[float] = []
    for s in (w / REF_WIDTH, h / REF_HEIGHT, 1.0):
        if all(abs(s - o) > 0.01 for o in out):
            out.append(s)
    return out


def set_client(w: int, h: int) -> None:
    """New run on a w x h client: forget the locked scale and any learned geometry."""
    global _scales, _locked
    _scales = scale_candidates(w, h)
    _locked = None
    reset_geometry()


def lock_scale(s: float) -> None:
    global _locked
    _locked = s


def locked_scale() -> Optional[float]:
    return _locked


def scales() -> list[float]:
    """The scales a search without a hint tries: the locked one, else every candidate."""
    return [_locked] if _locked is not None else list(_scales)


def current_scale() -> float:
    return _locked if _locked is not None else _scales[0]


def search_top(h: int, s: float) -> int:
    """First row a search without a hint looks at."""
    return max(0, min(int(h * SEARCH_TOP_FRAC), h - px(SEARCH_MIN_PX, s)))


@dataclass
class TrackReading:
    track_x0: int
    track_x1: int
    y0: int
    y1: int
    slider_x0: Optional[int]
    slider_x1: Optional[int]
    marker_x: Optional[float]
    scale: float = 1.0              # UI scale the bar was read at

    @property
    def slider_centre(self) -> Optional[float]:
        if self.slider_x0 is None:
            return None
        return (self.slider_x0 + self.slider_x1) / 2.0

    @property
    def slider_width(self) -> Optional[int]:
        if self.slider_x0 is None:
            return None
        return self.slider_x1 - self.slider_x0 + 1

    @property
    def error(self) -> Optional[float]:
        """marker - slider centre, px. Positive: fish is right of the slider centre."""
        if self.marker_x is None or self.slider_x0 is None:
            return None
        return self.marker_x - self.slider_centre

    # Aliases so a TrackReading can stand in for fischreel.ReelState in
    # fischcontrol.ReelController / fischreel.decide.
    @property
    def fish_x(self) -> Optional[float]:
        return self.marker_x

    @property
    def slider_w(self) -> int:
        return self.slider_width or 0

    @property
    def fish_inside(self) -> bool:
        return (self.marker_x is not None and self.slider_x0 is not None
                and self.slider_x0 <= self.marker_x <= self.slider_x1)


def _runs(row: np.ndarray, merge: int, min_len: int = 1) -> list[tuple[int, int]]:
    cols = np.flatnonzero(row)
    if cols.size == 0:
        return []
    br = np.flatnonzero(np.diff(cols) > merge)
    st = np.concatenate(([0], br + 1))
    en = np.concatenate((br, [cols.size - 1]))
    return [(int(cols[s]), int(cols[e])) for s, e in zip(st, en)
            if cols[e] - cols[s] + 1 >= min_len]


def _masks(frame: np.ndarray, y_lo: int, y_hi: int, x_lo: int, x_hi: int):
    """Track and slider masks for rows y_lo..y_hi, cols x_lo..x_hi (exclusive).

    Only the band (plus the probe rows) is converted to int, which is most of the
    cost: a full-frame int conversion alone is ~20ms at 1080p.
    """
    h = frame.shape[0]
    ys = np.arange(y_lo, y_hi)
    band = frame[y_lo:y_hi, x_lo:x_hi].astype(np.int16)
    s = band.sum(2)
    up = frame[np.clip(ys - TRACK_PROBE, 0, h - 1), x_lo:x_hi].astype(np.int16).sum(2)
    dn = frame[np.clip(ys + TRACK_PROBE, 0, h - 1), x_lo:x_hi].astype(np.int16).sum(2)
    # "Not green-dominant" rather than "purple": over sand the translucent track
    # reads ~(99,78,75), blue below green (measured live, 2026-10-02).
    g = band[..., 1]
    track = ((band.max(2) <= TRACK_MAX) &
             (g <= np.maximum(band[..., 0], band[..., 2]) + 8) &   # not rod VFX
             (s <= (up + dn) // 2 - TRACK_CONTRAST))
    slider = (band.min(2) >= SLIDER_MIN) & (band[..., 2] >= SLIDER_B_MIN)
    return track, slider


def _row_bar(track_row: np.ndarray,
             slider_row: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """(track_x0, track_x1, slider_x0, slider_x1) for one row, or None.

    Grown OUTWARD from the slider over SOLID dark runs (>=15px). Bridging every
    dark pixel instead chained the speckled floor texture onto the track's ends
    on a live capture, and pulled in the end-cap triangles on the recording.
    Gaps up to TRACK_GAP between solid runs are bridged (rod VFX over the track).
    """
    sruns = _runs(slider_row, BAR_MERGE, min_len=60)
    if not sruns:
        return None
    s0, s1 = max(sruns, key=lambda r: r[1] - r[0])
    truns = _runs(track_row, 3, min_len=15)
    x0 = s0
    for a, b in reversed([r for r in truns if r[1] < s0]):
        if x0 - b > TRACK_GAP:
            break
        x0 = a
    x1 = s1
    for a, b in [r for r in truns if r[0] > s1]:
        if a - x1 > TRACK_GAP:
            break
        x1 = b
    return x0, x1, s0, s1


def read_track_color(frame: np.ndarray,
                     hint: Optional[TrackReading] = None) -> Optional[TrackReading]:
    """SUPERSEDED by read_track (geometry-anchored); kept for comparison.

    Read the minigame from one RGB frame, or None if it is not on screen.

    `hint` is the previous reading. With it, only rows near the previous bar are
    scanned (~10x cheaper) and the track's ends must match the previous ones to
    within HINT_X_TOL -- the track never changes x, so a reading whose ends moved
    is an impostor (the catch-flash VFX produced exactly that on the recording).
    Without it, the whole lower part of the frame is searched.
    """
    h, w = frame.shape[:2]
    if hint is not None:
        y_lo = max(0, hint.y0 - HINT_Y_PAD)
        y_hi = min(h, hint.y1 + HINT_Y_PAD)
        x_lo = max(0, hint.track_x0 - HINT_X_PAD)
        x_hi = min(w, hint.track_x1 + HINT_X_PAD)
    else:
        y_lo, y_hi = int(h * SEARCH_TOP_FRAC), int(h * SEARCH_BOTTOM_FRAC)
        x_lo, x_hi = 0, w
    track, slider = _masks(frame, y_lo, y_hi, x_lo, x_hi)
    lo_span, hi_span = int(w * BAR_MIN_SPAN_FRAC), int(w * BAR_MAX_SPAN_FRAC)

    # Cheap prefilter: a bar row has at least ~lo_span bar pixels.
    counts = (track | slider).sum(1)
    rows = []
    for i in np.flatnonzero(counts >= lo_span * 0.6):
        rb = _row_bar(track[i], slider[i])
        if rb is None:
            continue
        x0, x1, s0, s1 = rb
        if lo_span <= x1 - x0 <= hi_span and s1 - s0 + 1 >= SLIDER_MIN_WIDTH_FRAC * (x1 - x0):
            rows.append((y_lo + int(i), x_lo + x0, x_lo + x1, x_lo + s0, x_lo + s1))
    if len(rows) < BAR_MIN_ROWS:
        return None

    # Largest block of near-consecutive rows that overlap. Ends are NOT required
    # to agree row to row (VFX shorten individual rows); medians handle that.
    blocks = [[rows[0]]]
    for r in rows[1:]:
        p = blocks[-1][-1]
        overlap = min(p[2], r[2]) - max(p[1], r[1])
        if r[0] - p[0] <= 3 and overlap > 0.8 * min(p[2] - p[1], r[2] - r[1]):
            blocks[-1].append(r)
        else:
            blocks.append([r])
    block = max(blocks, key=len)
    if not BAR_MIN_ROWS <= len(block) <= BAR_MAX_ROWS:
        return None
    y0, y1 = block[0][0], block[-1][0]
    tx0, tx1, sx0, sx1 = (int(np.median([r[k] for r in block])) for k in (1, 2, 3, 4))
    # Impostors (catch-flash VFX) moved BOTH ends; a real bar can have ONE end
    # wander when floor texture touches it (live: x0 500-589 vs 571).
    if hint is not None and (abs(tx0 - hint.track_x0) > HINT_X_TOL and
                             abs(tx1 - hint.track_x1) > HINT_X_TOL):
        return None

    # The marker is required. On the recording, all 31 bar-shaped readings that
    # had no marker were impostors (hotbar + power bar, catch toast); every true
    # minigame frame had one. If the marker is briefly occluded the caller just
    # sees None for that frame and should hold its last input.
    marker = find_marker(frame, tx0, tx1, y0, y1)
    if marker is None:
        return None
    return TrackReading(tx0, tx1, y0, y1, sx0, sx1, marker)


def find_marker(frame: np.ndarray, tx0: int, tx1: int, y0: int,
                y1: int, s: float = 1.0) -> Optional[float]:
    """Column-vote for the marker colour in the track band plus its overhang."""
    h = frame.shape[0]
    top = max(0, y0 - px(20, s))
    bot = min(h, y1 + px(16, s))
    band = frame[top:bot, tx0:tx1 + 1].astype(np.int16)
    r, g, b = band[..., 0], band[..., 1], band[..., 2]
    m = ((r >= MARKER_R[0]) & (r <= MARKER_R[1]) &
         (g >= MARKER_G[0]) & (g <= MARKER_G[1]) &
         (b >= MARKER_B[0]) & (b <= MARKER_B[1]) &
         (r - g >= MARKER_RG_MIN))
    col = m.sum(0)
    hit = np.flatnonzero(col >= px(MARKER_MIN_HEIGHT, s))
    if hit.size == 0:
        return None
    br = np.flatnonzero(np.diff(hit) > 2)
    st = np.concatenate(([0], br + 1))
    en = np.concatenate((br, [hit.size - 1]))
    best = None
    for a, e in zip(st, en):
        c0, c1 = hit[a], hit[e]
        if c1 - c0 + 1 > px(MARKER_MAX_WIDTH, s):
            continue
        mass = int(col[c0:c1 + 1].sum())
        if best is None or mass > best[0]:
            best = (mass, c0, c1)
    if best is None:
        return None
    _, c0, c1 = best
    xs = np.arange(c0, c1 + 1)
    wts = col[c0:c1 + 1].astype(float)
    return float(tx0 + (xs * wts).sum() / wts.sum())


# ======================================================================================
# Geometry-anchored reader (2026-10-02). This is the one the bot uses.
# ======================================================================================
#
# Why: measured at a dark red fishing spot (measure_logs/measure_20261002_170034),
# the translucent track turns LIGHTER than its surroundings during a progress boost
# (~(118,99,124) vs ~(85,60,94) around it), so read_track_color's "darker than the
# rows above and below" test fails there. What IS constant at every spot measured
# (recording, sandy spot, red spot):
#   * the bar's x-range: 571-1348 at 1920 wide -- it is screen-space UI, centred
#     on the client (571 + 1348 = 1919);
#   * the marker colour (232,193,209) and the slider being far brighter than the
#     track. Only the track's own colour depends on the scene.
# So: rows are found by the brightness EDGES at the track's two fixed ends, and the
# slider is split from the track per row, relative to that row's own dark and
# bright levels -- effectively recalibrating on every frame. No stored thresholds.

TRACK_HALF_W = 388.5            # centre to either end (571-1348 at 1920 wide)
GEO_ROWS = 31                   # track height in px
EDGE_MIN = 50                   # recording: bar rows 69-151, no-bar median 17
SPLIT_MIN = 150                 # min bright-vs-dark gap (channel sum) in a row

# Learned by fischcalib.GeometryLearner on the first reel, at the locked scale.
_learned_x: Optional[tuple[float, float]] = None    # track ends, fractions of width
_learned_dy: Optional[int] = None                   # progress box top - track.y1


def track_x(w: int, s: float = 1.0) -> tuple[int, int]:
    if _learned_x is not None and s == _locked:
        return int(round(w * _learned_x[0])), int(round(w * _learned_x[1]))
    c = (w - 1) / 2
    return int(round(c - TRACK_HALF_W * s)), int(round(c + TRACK_HALF_W * s))


def edge_scores(frame: np.ndarray, y_lo: int, y_hi: int,
                s: float = 1.0) -> np.ndarray:
    """Per-row min(|edge at left end|, |edge at right end|), channel sums.

    The hotbar and scenery have no edge at exactly these two columns; the bar has
    one at both. |edge| so a slider pinned against an end still counts.
    """
    x0, x1 = track_x(frame.shape[1], s)
    e = max(3, int(7 * s))
    d = max(20, int(25 * s))

    def col(a: int, b: int) -> np.ndarray:
        return np.median(frame[y_lo:y_hi, a:b].astype(np.int16).sum(2), axis=1)

    left = np.abs(col(x0 - e - 3, x0 - 3) - col(x0 + 3, x0 + d))
    right = np.abs(col(x1 + 3, x1 + e + 3) - col(x1 - d, x1 - 3))
    return np.minimum(left, right)


def read_track(frame: np.ndarray, hint: Optional[TrackReading] = None,
               scale: Optional[float] = None) -> Optional[TrackReading]:
    """Read the minigame from one RGB frame, or None if it is not on screen.

    `hint` (the previous reading) narrows the row search to around it and fixes
    the scale to the one it was read at. Without one, `scale` if given, else
    every scale in scales() is tried and the best-fitting reading is kept.
    """
    if hint is not None:
        return _read_track_at(frame, hint.scale, hint)[0]
    best, best_edge = None, -1.0
    for s in ([scale] if scale is not None else scales()):
        r, edge = _read_track_at(frame, s, None)
        if r is not None and edge > best_edge:
            best, best_edge = r, edge
    return best


def _read_track_at(frame: np.ndarray, s: float, hint: Optional[TrackReading]
                   ) -> tuple[Optional[TrackReading], float]:
    """(reading or None, edge score of the track's rows) at scale s."""
    h, w = frame.shape[:2]
    x0, x1 = track_x(w, s)
    e = max(3, int(7 * s))          # edge_scores' outer window
    if x0 - e - 3 < 0 or x1 + e + 3 > w:
        return None, 0.0            # the bar at this scale does not fit the frame
    rows_n = px(GEO_ROWS, s)
    dy = prog_top_dy(s)
    if hint is not None:
        y_lo = max(0, hint.y0 - px(HINT_Y_PAD, s))
        y_hi = min(h, hint.y1 + 1 + px(HINT_Y_PAD, s))
    else:
        y_lo, y_hi = search_top(h, s), int(h * SEARCH_BOTTOM_FRAC)
    if y_hi - y_lo < rows_n:
        return None, 0.0
    win = np.convolve(edge_scores(frame, y_lo, y_hi, s), np.ones(rows_n) / rows_n,
                      mode="valid")
    k = int(np.argmax(win))
    edge = float(win[k])
    if edge >= EDGE_MIN:
        y0 = y_lo + k
    else:
        # Fallback anchor: the progress box, which sits exactly PROG_TOP_DY below
        # the track. Live (2026-10-02 17:41, bot_20261002_174104): orange scenery
        # right of the bar and near-black scenery left of it left the track's
        # LEFT end with almost no edge (score ~35 at the true rows), so the bar
        # was lost the moment it settled -- while the box was found in every
        # frame. The slider, width and marker checks below still apply.
        p = find_progress(frame, y_lo + dy, y_hi + dy + px(PROG_ROWS, s), scale=s)
        if p is None:
            return None, 0.0
        y0 = p[1] - dy - rows_n + 1
        if y0 < 0:
            return None, 0.0
    y1 = y0 + rows_n - 1

    # Slider: per row, pixels above the midpoint between that row's dark level
    # (track) and bright level (slider), excluding green rod VFX.
    spans = []
    rows = range(y0 + px(6, s), y1 - px(5, s))
    for y in rows:
        row = frame[y, x0:x1 + 1].astype(np.int16)
        sums = row.sum(1)
        lo, hi = np.percentile(sums, [15, 97])
        if hi - lo < SPLIT_MIN:
            continue
        g = row[:, 1]
        bright = (sums > (lo + hi) / 2) & (g <= np.maximum(row[:, 0], row[:, 2]) + 8)
        runs = _runs(bright, px(BAR_MERGE, s), min_len=px(60, s))
        if runs:
            a, b = max(runs, key=lambda r: r[1] - r[0])
            spans.append((x0 + a, x0 + b))
    if len(spans) < len(rows) // 2:
        return None, 0.0
    sx0 = int(np.median([sp[0] for sp in spans]))
    sx1 = int(np.median([sp[1] for sp in spans]))
    if not (SLIDER_MIN_WIDTH_FRAC * (x1 - x0) <= sx1 - sx0 + 1
            <= SLIDER_MAX_WIDTH_FRAC * (x1 - x0)):
        # Too wide: live (2026-10-02, 17:28) a "slider" of 667-764px was read --
        # most likely a bright background showing through the translucent
        # track, so the whole row split as bright. Widest real one: ~431px.
        return None, 0.0

    # The marker is required: its colour is constant at every spot measured, and
    # bar-shaped impostors never carried one.
    marker = find_marker(frame, x0, x1, y0, y1, s)
    if marker is None:
        return None, 0.0
    return TrackReading(x0, x1, y0, y1, sx0, sx1, marker, scale=s), edge


# ======================================================================================
# Progress bar (2026-10-02)
# ======================================================================================
#
# The box under the minigame bar fills while the fish is inside the slider and
# drains while it is outside -- the game's own verdict, independent of how well
# the slider and marker were read. Measured on the recording and on live frames
# (identical geometry): x750-1169 at 1920 wide (centred, like the track), top row
# 38px below the track's bottom row, 11 rows tall, 1px bright border, opaque
# pink-white fill ~(246,194,239)..(229,212,241) from the left, translucent dark
# empty part.

PROG_HALF_W = 209.5             # centre to either end (750-1169 at 1920 wide)
PROG_TOP_DY = 38                # top row - track.y1
PROG_ROWS = 11
PROG_BRIGHT_MIN = 150           # min channel of fill / border pixels


def prog_top_dy(s: float = 1.0) -> int:
    """Rows from the track's bottom row to the progress box's top row."""
    if _learned_dy is not None and s == _locked:
        return _learned_dy
    return px(PROG_TOP_DY, s)


def _prog_x(w: int, s: float) -> tuple[int, int]:
    c, half = (w - 1) / 2, PROG_HALF_W * s
    if _learned_x is not None and s == _locked and s != 1.0:
        # At scale 1.0 the box was measured directly. Elsewhere it is inferred,
        # and it scales with the bar: follow the track's measured ends, which
        # also absorb a true scale slightly off the candidate that was locked.
        a, b = w * _learned_x[0], w * _learned_x[1]
        c, half = (a + b) / 2, PROG_HALF_W * (b - a) / (2 * TRACK_HALF_W)
    return int(round(c - half)), int(round(c + half))


def _border_rows(frame: np.ndarray, y_lo: int, y_hi: int, s: float) -> np.ndarray:
    """Bool per row: both of the box's end borders are bright on that row."""
    p0, p1 = _prog_x(frame.shape[1], s)
    o, i = px(5, s), px(3, s)
    band = frame[y_lo:y_hi]
    left = band[:, p0 - o:p0 + i].min(2).max(1) >= PROG_BRIGHT_MIN
    right = band[:, p1 - i:p1 + o].min(2).max(1) >= PROG_BRIGHT_MIN
    return left & right


def find_progress(frame: np.ndarray, y_lo: Optional[int] = None,
                  y_hi: Optional[int] = None,
                  scale: Optional[float] = None) -> Optional[tuple[float, int]]:
    """(fill 0..1, top row) of the progress box, or None if it is not on screen.

    The box is located by its two bright end borders -- a run of ~PROG_ROWS rows
    where both are bright -- so it does not need a track reading. That matters:
    it is what tells the bot the reel is still on when the bar itself is lost.
    `scale` defaults to current_scale().
    """
    s = current_scale() if scale is None else scale
    h, w = frame.shape[:2]
    rows_n = px(PROG_ROWS, s)
    tol = px(3, s)
    if y_lo is None:
        y_lo = search_top(h, s)
    if y_hi is None:
        y_hi = h
    y_lo, y_hi = max(0, y_lo), min(h, y_hi)
    if y_hi - y_lo < rows_n:
        return None
    rows = np.flatnonzero(_border_rows(frame, y_lo, y_hi, s))
    if rows.size == 0:
        return None
    runs = _runs(np.isin(np.arange(y_hi - y_lo), rows), 1, min_len=rows_n - tol)
    runs = [r for r in runs if r[1] - r[0] + 1 <= rows_n + tol]
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    top = y_lo + a
    p0, p1 = _prog_x(w, s)
    i0, i1 = px(3, s), px(2, s)
    mid = frame[top + i0:top + b - a - i1, p0 + i0:p1 - i1]
    if mid.size == 0:
        return None
    fill = np.median((mid.min(2) >= PROG_BRIGHT_MIN).sum(1)) / (p1 - p0 - i0 - i1)
    return float(min(1.0, fill)), top


# ======================================================================================
# Run-time geometry (fischcalib sets this per run)
# ======================================================================================


def set_geometry(track_x_frac: Optional[tuple[float, float]] = None,
                 prog_top_dy: Optional[int] = None) -> None:
    """Override the bar's x-range (fractions of width) and the progress box's
    offset below the track, at the locked scale. fischcalib calls this after
    measuring a reel."""
    global _learned_x, _learned_dy
    if track_x_frac is not None:
        _learned_x = track_x_frac
    if prog_top_dy is not None:
        _learned_dy = prog_top_dy


def reset_geometry() -> None:
    """Back to the measured defaults -- every run starts from these."""
    global _learned_x, _learned_dy
    _learned_x = _learned_dy = None
