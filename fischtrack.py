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
    method: str = "colour"          # "geo" if the colour-agnostic detectors read it
    slider_rgb: Optional[tuple] = None   # geo slider's median colour (next frame's prior)

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
               scale: Optional[float] = None,
               expect_w: Optional[float] = None) -> Optional[TrackReading]:
    """Read the minigame from one RGB frame, or None if it is not on screen.

    `hint` (the previous reading) narrows the row search to around it and fixes
    the scale to the one it was read at. Without one, `scale` if given, else
    every scale in scales() is tried and the best-fitting reading is kept.
    `expect_w` (recent slider width, px) steadies the geometry slider reading.
    """
    if hint is not None:
        return _read_track_at(frame, hint.scale, hint, expect_w)[0]
    best, best_edge = None, -1.0
    for s in ([scale] if scale is not None else scales()):
        r, edge = _read_track_at(frame, s, None, expect_w)
        if r is not None and edge > best_edge:
            best, best_edge = r, edge
    return best


def _read_track_at(frame: np.ndarray, s: float, hint: Optional[TrackReading],
                   expect_w: Optional[float] = None
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
    anchored = edge < EDGE_MIN          # rows from the progress box, not the edges
    if not anchored:
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

    # Slider and marker: the colour methods first (proven on the Fabulous Rod's
    # white-pink skin), then the colour-agnostic geometry methods for other skins
    # (2026-10-03: Duskwire = black gradient slider + white marker line; Crew Rod =
    # light grey slider + dark grey marker). Each falls back on its own: the Crew
    # Rod's slider reads by colour, its marker only by geometry.
    method = "colour"
    slider_rgb = None
    marker = find_marker(frame, x0, x1, y0, y1, s)
    if marker is None:
        mg = find_marker_geo(frame, x0, x1, y0, y1, s)
        if mg is None:
            return None, 0.0
        marker, method = mg[0], "geo"
    slider = _slider_colour(frame, x0, x1, y0, y1, s)
    if slider is None:
        # Without a previous reading, the track centre: every reel starts with
        # the slider there.
        centre = hint.slider_centre if hint is not None and hint.slider_centre \
            is not None else (x0 + x1) / 2
        sg = find_slider_geo(frame, x0, x1, y0, y1, s, marker_x=marker,
                             expect_w=expect_w, expect_c=centre,
                             expect_rgb=hint.slider_rgb if hint is not None else None)
        if sg is None:
            return None, 0.0
        slider, method, slider_rgb = (sg[0], sg[1]), "geo", sg[4]
    if method == "geo":
        # The pink marker colour no longer vouches for this being the minigame.
        # Two checks instead, measured on the recording: the track outside the
        # slider must be ONE even colour (real bars: median 15, 95% <= 27, the
        # new skins 8-27; the catch message + power bar between reels, which read
        # as a "bar" for 16 frames in a row: >= 63), and the progress box must
        # be under the track. And the rows must come from the track's end edges,
        # not the progress-box fallback: a catch message over the power bar
        # passed both other checks with an edge of 5 (real bars: >= 69 on the
        # recording, 106-241 for Duskwire / Crew Rod live).
        if anchored:
            return None, 0.0
        if _track_unevenness(frame, x0, x1, y0, y1, s, slider) > GEO_TRACK_MAX_UNEVEN:
            return None, 0.0
        # expect_top: on a light floor the box's end borders do not stand out
        # (saved_logs/20261003_115038: a whole Duskwire reel went unread, 4s,
        # because of this check alone -- fish, slider and track all passed).
        if find_progress(frame, y1 + dy - px(6, s), y1 + dy + px(PROG_ROWS + 6, s),
                         scale=s, expect_top=y1 + dy) is None:
            return None, 0.0
    return TrackReading(x0, x1, y0, y1, slider[0], slider[1], marker, scale=s,
                        method=method, slider_rgb=slider_rgb), edge


def _slider_colour(frame: np.ndarray, x0: int, x1: int, y0: int, y1: int,
                   s: float) -> Optional[tuple[int, int]]:
    """Slider by colour: per row, pixels above the midpoint between that row's
    dark level (track) and bright level (slider), excluding green rod VFX. Only
    works for a slider much brighter than the track (the Fabulous Rod's skin)."""
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
        return None
    sx0 = int(np.median([sp[0] for sp in spans]))
    sx1 = int(np.median([sp[1] for sp in spans]))
    if not (SLIDER_MIN_WIDTH_FRAC * (x1 - x0) <= sx1 - sx0 + 1
            <= SLIDER_MAX_WIDTH_FRAC * (x1 - x0)):
        # Too wide: live (2026-10-02, 17:28) a "slider" of 667-764px was read --
        # most likely a bright background showing through the translucent
        # track, so the whole row split as bright. Widest real one: ~431px.
        return None
    return sx0, sx1


# --------------------------------------------------------------------------------------
# Colour-agnostic slider / marker (2026-10-03)
# --------------------------------------------------------------------------------------
#
# Rods have their own reel-bar skins. Measured from the user's bar crops at UI
# scale 0.44 (saved_logs/20261003_083155 Duskwire, 20261003_083231 Crew Rod):
#   * Duskwire: slider is a grey -> black gradient (its left third is nearly the
#     track's colour), 58px = 15% of the track (low Control: -0.2 +0.05); marker
#     is a thin WHITE line with a music note.
#   * Crew Rod: light grey slider, 92px; marker is a DARK grey capsule.
# So: the slider is found by colour STEPS along the track, the marker as a thin
# column that stands out both above AND below the track. On the Fabulous Rod
# recording the geometry marker agrees with the colour one to 0.1px (median,
# 245 frames, none off by >8px).

GEO_SLIDER_MIN_FRAC = 0.06      # Duskwire's slider is 0.15 of the track
GEO_EDGE_MIN = 25               # colour step (channel-sum) that can be a slider edge
GEO_SLIDER_MIN_CONTRAST = 40    # mean inside-vs-track distance minus outside's
GEO_WIDEST_WITHIN = 0.9         # see find_slider_geo
GEO_NEAR_WITHIN = 0.6           # candidates considered for continuity (x top score)
GEO_RGB_PENALTY = 0.5           # score lost per unit of colour change vs last frame
GEO_MARKER_MIN = 50             # marker column contrast, above AND below the track
GEO_TRACK_MAX_UNEVEN = 45       # see _track_unevenness / the "geo" checks


def _track_unevenness(frame: np.ndarray, x0: int, x1: int, y0: int, y1: int,
                      s: float, slider: tuple[int, int]) -> float:
    """Mean distance (channel sum) of the track's columns OUTSIDE the slider from
    their median colour. A real track is one translucent colour even over busy
    scenery; text and scenery that only look bar-shaped are not."""
    band = frame[y0 + px(6, s):y1 - px(5, s) + 1, x0:x1 + 1].astype(np.int16)
    if band.shape[0] < 1:
        return 1e9
    P = np.median(band, axis=0)
    m = max(2, px(4, s))
    a, b = slider[0] - x0, slider[1] - x0 + 1
    outside = np.vstack([P[:max(0, a - m)], P[min(len(P), b + m):]])
    if len(outside) < 0.1 * len(P):
        return 1e9
    return float(np.abs(outside - np.median(outside, 0)).sum(1).mean())


def _sliding_median(a: np.ndarray, r: int) -> np.ndarray:
    """Per-row median over [i-r, i+r] (edge-padded). a: (W, 3)."""
    pad = np.pad(a, ((r, r), (0, 0)), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * r + 1, axis=0)
    return np.median(win, axis=2)


def find_marker_geo(frame: np.ndarray, x0: int, x1: int, y0: int, y1: int,
                    s: float) -> Optional[tuple[float, float]]:
    """(x, score) of the fish marker by shape, whatever its colour: the one thin
    column that differs from its surroundings in a band just ABOVE the track and
    in a band just BELOW it (the marker overhangs the track by ~15px at scale 1;
    rod VFX and text usually cover only one side)."""
    h = frame.shape[0]
    a0, a1 = max(0, y0 - px(14, s)), max(0, y0 - px(6, s))
    b0, b1 = min(h, y1 + px(5, s)), min(h, y1 + px(12, s))
    if a1 - a0 < 1 or b1 - b0 < 1:
        return None
    A = np.median(frame[a0:a1 + 1, x0:x1 + 1].astype(np.int16), axis=0)
    B = np.median(frame[b0:b1 + 1, x0:x1 + 1].astype(np.int16), axis=0)
    r = px(20, s)
    cA = np.abs(A - _sliding_median(A, r)).sum(1)
    cB = np.abs(B - _sliding_median(B, r)).sum(1)
    sc = np.minimum(cA, cB)
    k = max(1, px(3, s))
    sc = np.convolve(sc, np.ones(k) / k, mode="same")
    i = int(np.argmax(sc))
    if sc[i] < GEO_MARKER_MIN:
        return None
    lo, hi = max(0, i - px(12, s)), min(len(sc), i + px(12, s) + 1)
    seg = sc[lo:hi]
    wts = np.where(seg >= 0.5 * sc[i], seg, 0)
    if (wts > 0).sum() > px(MARKER_MAX_WIDTH, s):
        return None                                  # too wide to be the marker
    return x0 + lo + float(np.arange(len(seg)) @ wts / wts.sum()), float(sc[i])


def find_slider_geo(frame: np.ndarray, x0: int, x1: int, y0: int, y1: int, s: float,
                    min_frac: float = GEO_SLIDER_MIN_FRAC,
                    max_frac: float = SLIDER_MAX_WIDTH_FRAC,
                    marker_x: Optional[float] = None,
                    expect_w: Optional[float] = None,
                    expect_c: Optional[float] = None,
                    expect_rgb: Optional[tuple[float, float, float]] = None
                    ) -> Optional[tuple[int, int, float, float, tuple]]:
    """(x0, x1, contrast) of the slider by colour steps, whatever its colour.

    Candidate edges are the strongest colour steps along the track's middle
    rows (plus the track's ends, for a slider pinned against one). Each pair of
    plausible width is scored by how far every inside column is from the track
    colour (the median outside), minus how far the outside columns are from it
    -- a mean, so a gradient slider still scores. Steps at the fish marker are
    ignored: on Duskwire its white line was the strongest step and split the
    slider in two.

    `expect_w` (the recent slider width) is preferred: a much NARROWER free
    reading is part of a slider (Duskwire's dark half), a clearly better WIDER
    one is a progress boost (Fabulous Rod 252 -> 437px).

    `expect_c` (the previous slider centre, or the track centre at reel start,
    where the slider always begins) picks between good candidates.

    `expect_rgb` (the previous reading's slider colour) penalises candidates
    that look different: a skin's slider keeps its colour all reel. Rod VFX over
    one end of the track (the user's white star effect, brightening it to
    ~225) read as a "slider" of Duskwire's width ~150px from the real near-black
    one until this was added.

    Returns (x0, x1, contrast, outside spread, inside median colour)."""
    if expect_w:
        span = x1 - x0 + 1
        free = find_slider_geo(frame, x0, x1, y0, y1, s, min_frac, max_frac, marker_x,
                               None, expect_c, expect_rgb)
        tol = max(px(5, s), 0.12 * expect_w)
        con = find_slider_geo(frame, x0, x1, y0, y1, s,
                              max(min_frac, (expect_w - tol) / span),
                              min(max_frac, (expect_w + tol) / span), marker_x,
                              None, expect_c, expect_rgb)
        if con is None or free is None:
            return con or free
        if free[1] - free[0] + 1 < expect_w - tol:
            return con
        return free if con[2] < 0.8 * free[2] else con

    band = frame[y0 + px(6, s):y1 - px(5, s) + 1, x0:x1 + 1].astype(np.int16)
    if band.shape[0] < 2:
        return None
    P = np.median(band, axis=0)                       # (W, 3) column colours
    W = P.shape[0]
    k = max(2, px(5, s))
    cs = np.cumsum(np.vstack([np.zeros((1, 3)), P]), axis=0)
    xs = np.arange(k, W - k)
    D = np.abs((cs[xs] - cs[xs - k]) / k - (cs[xs + k] - cs[xs]) / k).sum(1)
    # Steps at the fish marker are NOT masked: the fish often sits on a slider
    # edge, which then is the only step there (Duskwire, 5.66s in the user's
    # crops). Continuity, colour and width priors stop its line from splitting
    # the slider instead.
    peaks = [(D[i], int(xs[i])) for i in range(1, len(D) - 1)
             if D[i] >= GEO_EDGE_MIN and D[i] >= D[i - 1] and D[i] >= D[i + 1]]
    bounds = sorted(set([x for _, x in sorted(peaks, reverse=True)[:14]] + [0, W]))
    m = max(2, px(4, s))
    cols = np.arange(W)
    scored = []
    for i, a in enumerate(bounds):
        for b in bounds[i + 1:]:
            if not (min_frac * W <= b - a <= max_frac * W):
                continue
            inside = P[a + m:b - m]
            if marker_x is not None:
                keep = np.abs(cols[a + m:b - m] - (marker_x - x0)) > px(6, s)
                inside = inside[keep]
            outside = np.vstack([P[:max(0, a - m)], P[min(W, b + m):]])
            if len(inside) < 3 or len(outside) < 0.15 * W:
                continue
            co = np.median(outside, 0)
            # Median, not mean: rod VFX glowing over part of the track (the
            # user's star effect brightened its right fifth from ~88 to ~225)
            # must not sink the real slider's score.
            dout = float(np.median(np.abs(outside - co).sum(1)))
            contrast = np.abs(inside - co).sum(1).mean() - dout
            ci = np.median(inside, 0)
            score = contrast
            if expect_rgb is not None:
                score -= GEO_RGB_PENALTY * float(np.abs(ci - np.asarray(expect_rgb)).sum())
            scored.append((score, a, b, dout, contrast, tuple(float(v) for v in ci)))
    if not scored:
        return None
    if max(t[4] for t in scored) < GEO_SLIDER_MIN_CONTRAST:
        return None
    top = max(t[0] for t in scored)
    if top <= 0:
        # every candidate looks unlike the slider of the previous frame
        return None
    if expect_c is not None:
        # Continuity: among good candidates, the one nearest where the slider
        # was (it moves a few px per frame); widest breaks near-ties, so a
        # gradient slider is still read whole.
        c = expect_c - x0
        pool = [t for t in scored if t[0] >= GEO_NEAR_WITHIN * top]
        near = min(abs((t[1] + t[2]) / 2 - c) for t in pool)
        pool = [t for t in pool if abs((t[1] + t[2]) / 2 - c) <= near + px(12, s)]
        best = max(pool, key=lambda t: (t[2] - t[1], t[0]))
    else:
        # A gradient slider scores slightly better as just its dark half; the
        # whole slider is the WIDEST pair that scores close to the best.
        best = max((t for t in scored if t[0] >= GEO_WIDEST_WITHIN * top),
                   key=lambda t: (t[2] - t[1], t[0]))
    _, a, b, dout, contrast, ci = best
    return x0 + a, x0 + b - 1, float(contrast), float(dout), ci


# --------------------------------------------------------------------------------------
# Per-reel tracker for non-default skins (2026-10-03, second pass)
# --------------------------------------------------------------------------------------
#
# Live with Duskwire / Crew Rod (saved_logs/20261003_090708..090805) the one-frame
# geometry reader above still lost the bar: the track is TRANSLUCENT, so over the
# user's red-floor + white-wall spot its left part read (45,27,25) and its right
# part (61,61,61) -- a "slider" to any single-frame method; the Crew Rod's slider
# turns translucent brown whenever the fish is outside it; and the rod's white
# star effect crossed the bar and was taken for the fish. Every reel was lost or
# barely won.
#
# Live traces showed that learning the track from guessed slider positions
# gradually learned the slider itself as background: Crew Rod's width shrank
# from 174px to 69px and its reported position could be ~250px wrong. The tracker
# now reads the slider's full-height rectangle independently each frame, using
# its matching upper/lower rims outside the shorter track. It retains the start
# width when effects obscure those rims; only a clearly bounded rectangle can
# change it. The fish is the straight vertical mark above, on and below the
# track, with both overhangs matching the colour learned at this reel's start.
# Every reel starts with slider and fish centred, which gives the initial state.

TRK_RIM_MIN = 35           # paired horizontal edges, after vertical-colour mismatch
TRK_RIM_COVERAGE = 0.50    # minimum slider fraction with visible top AND bottom rims
TRK_RIM_WIDTH_COVERAGE = 0.82  # stronger evidence needed to change the learned width
TRK_RIM_Y_PAD = 5          # end-edge row estimate may be a few pixels off
TRK_MARKER_MIN = 30         # vertical-mark score (after the colour penalty)
TRK_MARKER_RGB_PENALTY = 0.5
TRK_MARKER_RGB_MAX = 80    # each overhang must match the learned fish (channel sum)
TRK_MARKER_RGB_SAMPLES = 5  # fish readings (bar settled) its colour is learned from
TRK_SLIDER_SPEED = 1.2      # track widths / s searched (recording: p99 0.54, i.e. 420px/s)
TRK_FISH_SPEED = 0.8        # ... for the fish (recording: max 0.31, 244px/s)
TRK_START_PAIR_FRAC = 0.2 # start width: widest symmetric step pair >= this x the best
TRK_WIDTH_WINDOW = 9       # recent edge-to-edge slider widths kept ...
TRK_WIDTH_MIN_N = 3         # ... and needed before the width is corrected


class SkinTracker:
    """Follows the slider and fish of a non-default reel-bar skin through one reel.

    Built from the reel's first reading; read() then replaces read_track for the
    rest of the reel. Frames may be row bands: pass the band's y offset."""

    def __init__(self, frame: np.ndarray, r: TrackReading, y_off: int = 0,
                 now: float = 0.0, centred: bool = True):
        self.s = s = r.scale
        self.x0, self.x1 = r.track_x0, r.track_x1
        self.W = self.x1 - self.x0 + 1
        self.y0, self.y1 = r.y0, r.y1
        self.t = now
        self.why = ""                   # why the last read() returned None
        self.edge = float(EDGE_MIN)     # end-edge score at the current rows
        P = self._profile(frame, y_off)
        # The one-frame reading only has to cover the centre: it read Duskwire's
        # slider as just its dark half (centre ~30px off) at reel start.
        tc = (self.x0 + self.x1) / 2
        if centred and r.slider_x0 is not None \
                and r.slider_x0 - px(10, s) <= tc <= r.slider_x1 + px(10, s):
            a, b = self._centred_slider(P)
        else:
            a, b = r.slider_x0 - self.x0, r.slider_x1 - self.x0 + 1
        self.w = b - a
        self.base_w = self.w  # boosts can shrink back here, never to an icon's width
        self.c = (a + b) / 2
        self.widths: list[int] = []
        m = self._find_marker(frame, y_off, near=(a + b) / 2, reach=px(30, s),
                              use_rgb=False) if centred else None
        self.m = m if m is not None else (r.marker_x - self.x0
                                          if r.marker_x is not None else self.c)
        # The fish's colour, from its first few readings once the bar has stopped
        # sliding in. One sample at reel start was wrong on the recording (the
        # bar mid-slide: (148,118,159) for the pink (232,193,209) marker), and
        # the colour test then rejected the real fish for the whole reel.
        self.mrgb: Optional[np.ndarray] = None
        self._mrgb_samples: list[np.ndarray] = []

    # -- state ------------------------------------------------------------------------
    def reading(self) -> TrackReading:
        """The current state (slider, fish) as a reading."""
        a = int(round(self.c - self.w / 2))
        return TrackReading(self.x0, self.x1, self.y0, self.y1, self.x0 + a,
                            self.x0 + a + self.w - 1, self.x0 + self.m, scale=self.s,
                            method="tracker")

    def _rows(self, y_off: int) -> tuple[int, int]:
        s = self.s
        return self.y0 + px(6, s) - y_off, self.y1 - px(5, s) + 1 - y_off

    def _profile(self, frame: np.ndarray, y_off: int) -> np.ndarray:
        r0, r1 = self._rows(y_off)
        return np.median(frame[r0:r1, self.x0:self.x1 + 1].astype(np.int16), axis=0)

    def _steps(self, P: np.ndarray, spread: int = 2, k: Optional[int] = None) -> np.ndarray:
        """Colour-step strength (mean of k columns either side) at each column
        boundary (0..W), max over +-spread."""
        W = self.W
        k = max(2, px(4, self.s)) if k is None else k
        pc = np.vstack([np.zeros((1, 3)), np.cumsum(P, axis=0)])
        D = np.zeros(W + 1)
        xs = np.arange(k, W - k + 1)
        D[xs] = np.abs((pc[xs] - pc[xs - k]) / k - (pc[xs + k] - pc[xs]) / k).sum(1)
        if not spread:
            return D
        return np.maximum.reduce([np.roll(D, i) for i in range(-spread, spread + 1)])

    def _centred_slider(self, P: np.ndarray) -> tuple[int, int]:
        """The slider at reel start: centred on the track, its half-width the one
        with the strongest PAIR of colour steps at centre +- h (Duskwire's grey
        -> black gradient read as only its dark half by the one-frame reader).
        The slider's own icons can make a STRONGER symmetric pair than its
        edges (arrows: 457 vs 153 on a recoloured dark slider), but they are
        always inside it -- so the widest pair that is still clearly a step."""
        W = self.W
        Dm = self._steps(P)
        c = W / 2
        pairs = []
        for h in range(int(GEO_SLIDER_MIN_FRAC / 2 * W), int(SLIDER_MAX_WIDTH_FRAC / 2 * W)):
            lo, hi = int(round(c - h)), int(round(c + h))
            if lo < 0 or hi > W:
                break
            pairs.append((min(Dm[lo], Dm[hi]), h))
        best = max(p[0] for p in pairs)
        ok = [h for sc, h in pairs if sc >= max(2 * GEO_EDGE_MIN, TRK_START_PAIR_FRAC * best)]
        bh = max(ok) if ok else max(pairs)[1]
        return int(round(c - bh)), int(round(c + bh))

    def _marker_rgb(self, frame: np.ndarray, y_off: int, m: float) -> np.ndarray:
        s, h = self.s, frame.shape[0]
        x = int(round(self.x0 + m))
        above = frame[max(0, self.y0 - px(14, s) - y_off):
                      max(0, self.y0 - px(5, s) - y_off), x - 1:x + 2]
        below = frame[min(h, self.y1 + px(5, s) - y_off):
                      min(h, self.y1 + px(13, s) - y_off), x - 1:x + 2]
        px_ = np.vstack([above.reshape(-1, 3), below.reshape(-1, 3)])
        return np.median(px_, 0) if len(px_) else np.zeros(3)

    def _find_slider(self, frame: np.ndarray, y_off: int, dt: float):
        """Locate the rectangle by its paired horizontal rims and vertical ends.

        The slider protrudes ~4px beyond the 31px track. Its upper/lower rims
        match its middle columns (including gradient and translucent skins),
        and differ from the scene just outside. The track itself stops sooner;
        diagonal stars seldom satisfy both rims across a slider-sized window.
        No pixels from a guessed slider position are learned as background.
        Returns track-relative [a,b), client y0 and visible-rim coverage.
        """
        s, W = self.s, self.W
        h = frame.shape[0]
        nrows = self.y1 - self.y0 + 1
        best = None
        margin = max(px(5, s), 0.04 * self.w)
        flank_n = px(12, s)
        # This central band stays inside the track throughout the small rim-row
        # search. Reuse its profile/texture instead of taking eleven medians.
        r0, r1 = self._rows(y_off)
        body = frame[r0:r1, self.x0:self.x1 + 1].astype(np.int16)
        if not len(body):
            return None
        P = np.median(body, axis=0)
        texture = np.median(np.abs(body - P).sum(2), axis=0)
        body_steps = self._steps(P, spread=0)
        pad = px(TRK_RIM_Y_PAD, s)
        offsets = [0] + [dy for k in range(1, pad + 1) for dy in (-k, k)]
        for dy in offsets:
            y = self.y0 + dy - y_off
            end = y + nrows - 1
            if y - px(8, s) < 0 or end + px(10, s) > h:
                continue

            def med(a, b):
                # At small UI scales the rim is just one pixel tall.
                return np.median(frame[a:max(a + 1, b), self.x0:self.x1 + 1]
                                 .astype(np.int16), axis=0)

            top = med(y - px(3, s), y)
            bottom = med(end + px(2, s), end + px(5, s))
            above = med(y - px(8, s), y - px(5, s))
            below = med(end + px(7, s), end + px(10, s))
            contrast = np.minimum(np.abs(top - above).sum(1),
                                  np.abs(bottom - below).sum(1))
            mismatch = np.maximum(np.abs(top - P).sum(1),
                                  np.abs(bottom - P).sum(1))
            good = contrast - mismatch > TRK_RIM_MIN
            # A translucent brown slider over reddish scenery can have weaker
            # outer edges than its own tint across the track. Its body is still
            # vertically flat, and agrees with both protruding rims; scenery
            # through the shorter track does not have that full-height shape.
            good |= (mismatch < 60) & (contrast > 10) & (texture < 5)
            if good.sum() < GEO_SLIDER_MIN_FRAC * W * TRK_RIM_COVERAGE:
                continue
            E = np.maximum(body_steps,
                           self._steps((top + bottom) / 2, spread=0))
            vertical = np.minimum(self._steps(top), self._steps(bottom))
            peaks = np.flatnonzero((E[1:-1] >= E[:-2]) & (E[1:-1] >= E[2:])
                                   & (E[1:-1] >= GEO_EDGE_MIN)) + 1
            # Gradients/overlaid icons move a step's peak a couple of pixels.
            # Rim transitions add the physical ends even in those frames.
            rims = np.flatnonzero(np.diff(good.astype(np.int8))) + 1
            bounds = np.unique(np.r_[0, W, peaks, rims])
            E = np.maximum.reduce([np.roll(E, k) for k in (-2, -1, 0, 1, 2)])
            ii, jj = np.triu_indices(len(bounds), 1)
            a, b = bounds[ii], bounds[jj]
            width = b - a
            plausible = ((width >= max(GEO_SLIDER_MIN_FRAC * W, 0.9 * self.base_w))
                         & (width <= SLIDER_MAX_WIDTH_FRAC * W))
            a, b, width = a[plausible], b[plausible], width[plausible]
            if not len(a):
                continue
            cs = np.r_[0, np.cumsum(good)]
            coverage = (cs[b] - cs[a]) / width
            left = (cs[a] - cs[np.maximum(0, a - flank_n)]) / np.maximum(1, np.minimum(a, flank_n))
            right = (cs[np.minimum(W, b + flank_n)] - cs[b]) / np.maximum(1, np.minimum(W - b, flank_n))
            flank = np.maximum(left, right)
            edge = np.minimum(np.where(a == 0, E[b], E[a]),
                              np.where(b == W, E[a], E[b]))
            close_width = np.abs(width - self.w) <= margin
            # A boost may widen AND later shrink the bar. Permit that only when
            # the whole rectangle is independently visible with clean flanks.
            vertical_edge = np.minimum(np.where(a == 0, vertical[b], vertical[a]),
                                       np.where(b == W, vertical[a], vertical[b]))
            clear_of_fish = (np.abs(a - self.m) > px(10, s)) & (np.abs(b - self.m) > px(10, s))
            new_width = ((coverage >= TRK_RIM_WIDTH_COVERAGE) & (flank <= 0.15)
                         & (vertical_edge >= GEO_EDGE_MIN) & clear_of_fish
                         & (a > 0) & (b < W))
            valid = (coverage >= TRK_RIM_COVERAGE) & (edge >= GEO_EDGE_MIN) & (close_width | new_width)
            valid &= (coverage >= 0.65) | (vertical_edge >= GEO_EDGE_MIN)
            score = 100 * coverage - 60 * flank + 0.3 * np.minimum(40, edge)
            score -= np.where(close_width, 0.8 * np.abs(width - self.w), 0.0)
            # Continuity breaks ambiguous ties; a clearly visible rectangle can
            # still recover anywhere after a dropout or a bad one-frame hint.
            reach = px(40, s) + TRK_SLIDER_SPEED * W * dt
            score -= np.minimum(12, np.maximum(0, np.abs((a + b) / 2 - self.c) - reach) / max(1, px(4, s)))
            score -= 0.1 * abs(dy)
            score = np.where(valid, score, -1e9)
            i = int(np.argmax(score))
            if score[i] < 55:
                continue
            result = (float(score[i]), int(a[i]), int(b[i]), y + y_off, float(coverage[i]))
            if score[i] >= 100 and coverage[i] >= TRK_RIM_WIDTH_COVERAGE:
                # Settled unobscured rows need no eleven-position rescan.
                return result[1:]
            if best is None or result[0] > best[0]:
                best = result
        return best[1:] if best is not None else None

    # -- per frame ----------------------------------------------------------------------
    def read(self, frame: np.ndarray, y_off: int = 0,
             now: Optional[float] = None) -> Optional[TrackReading]:
        s, W = self.s, self.W
        dt = 0.05 if now is None else max(0.0, now - self.t)
        y_before = self.y0
        self._follow_rows(frame, y_off)
        slider = self._find_slider(frame, y_off, dt)
        if slider is None:
            self.why = "slider edges obscured"
            return None
        sa, sb, y0, coverage = slider
        self.y1 += y0 - self.y0
        self.y0 = y0
        mk = self._find_marker(frame, y_off, near=self.m,
                               reach=px(40, s) + TRK_FISH_SPEED * W * dt)
        if mk is None:
            self.why = "no fish"
            return None
        # Only an independently visible rectangle can recalibrate width. Icons,
        # shadows and VFX used to shrink it from 174px to 69px by being learned
        # as track background. There is no background learning in this reader.
        if coverage >= TRK_RIM_WIDTH_COVERAGE and sa > 0 and sb < W:
            self.widths = (self.widths + [sb - sa])[-TRK_WIDTH_WINDOW:]
            if len(self.widths) >= TRK_WIDTH_MIN_N:
                self.w = int(round(float(np.median(self.widths))))
        self.why = ""
        if self.mrgb is None and self.y0 == y_before:
            self._mrgb_samples.append(self._marker_rgb(frame, y_off, mk))
            if len(self._mrgb_samples) >= TRK_MARKER_RGB_SAMPLES:
                self.mrgb = np.median(self._mrgb_samples, axis=0)
        self.c, self.m, self.t = (sa + sb) / 2, mk, (self.t if now is None else now)
        return TrackReading(self.x0, self.x1, self.y0, self.y1, self.x0 + sa,
                            self.x0 + sb - 1, self.x0 + mk, scale=s, method="tracker")

    def _follow_rows(self, frame: np.ndarray, y_off: int) -> None:
        """Re-find the rows by the track's end edges, within the bar's entrance
        slide of the current ones (~90px up over ~0.5s on the recording). Stay
        unless another position is clearly stronger (a window 6px off the true
        rows scores ~0.8 of it; 1px off, ~0.97): live, the edge score at the
        RIGHT rows dipped to 38-50 over the red floor -- under EDGE_MIN."""
        s, h = self.s, frame.shape[0]
        rows_n = self.y1 - self.y0 + 1
        pad = px(HINT_Y_PAD, s)
        lo = max(0, self.y0 - pad - y_off)
        hi = min(h, self.y1 + 1 + pad - y_off)
        if hi - lo < rows_n:
            return
        win = np.convolve(edge_scores(frame, lo, hi, s), np.ones(rows_n) / rows_n,
                          mode="valid")
        k = int(np.argmax(win))
        here = self.y0 - y_off - lo
        self.edge = float(win[here]) if 0 <= here < len(win) else 0.0
        if win[k] < EDGE_MIN or (0 <= here < len(win) and win[here] >= 0.87 * win[k]):
            return
        self.y0 = lo + k + y_off
        self.y1 = self.y0 + rows_n - 1
        self.edge = float(win[k])

    def _find_marker(self, frame: np.ndarray, y_off: int, near: float, reach: float,
                     use_rgb: bool = True) -> Optional[float]:
        """Fish x (track coords): the column that stands out from its neighbours
        above the track, below it AND on it -- one straight vertical mark -- in
        the fish's colour. Searched within `reach` of `near` first."""
        s, W, h = self.s, self.W, frame.shape[0]
        r = px(20, s)
        lo = max(0, int(near - reach) - r)
        hi = min(W, int(near + reach) + r + 1)
        if hi - lo < 2 * r + 2:
            lo, hi = 0, W

        def band(ya: int, yb: int):
            ya, yb = max(0, ya - y_off), min(h, yb - y_off)
            if yb - ya < 1:
                return None, None
            C = np.median(frame[ya:yb, self.x0 + lo:self.x0 + hi].astype(np.int16), axis=0)
            return np.abs(C - _sliding_median(C, r)).sum(1), C

        cA, A = band(self.y0 - px(14, s), self.y0 - px(5, s))
        cB, B = band(self.y1 + px(5, s), self.y1 + px(13, s))
        cT1, _ = band(self.y0 + px(2, s), self.y0 + px(10, s))
        cT2, _ = band(self.y1 - px(9, s), self.y1 - px(1, s))
        if cA is None or cB is None or cT1 is None or cT2 is None:
            return None
        sc = np.minimum.reduce([cA, cB, np.maximum(cT1, cT2)])
        if use_rgb and self.mrgb is not None:
            distance = np.maximum(np.abs(A - self.mrgb).sum(1),
                                  np.abs(B - self.mrgb).sum(1))
            # A bright diagonal star can have enough contrast to beat the grey
            # capsule even after the old soft colour penalty. Both overhangs
            # must actually match the fish colour learned at this reel's start.
            sc = np.where(distance <= TRK_MARKER_RGB_MAX,
                          np.maximum(0, sc - TRK_MARKER_RGB_PENALTY * distance), 0)
        k = max(1, px(3, s))
        raw_score = sc
        sc = np.convolve(sc, np.ones(k) / k, mode="same")
        idx = np.arange(lo, hi)
        ok = np.abs(idx - near) <= reach
        i = int(np.argmax(np.where(ok, sc, -1e9)))
        if sc[i] < TRK_MARKER_MIN:
            if not use_rgb or self.mrgb is None:
                return None
            # A thin white effect can cover the capsule's centre but leave its
            # two grey sides visible. Smoothing three columns diluted both
            # narrow matches below threshold (104218, 7.10s). Require at least
            # two independently colour-matched columns in one marker-width
            # neighbourhood before using their unsmoothed evidence.
            i = int(np.argmax(np.where(ok, raw_score, -1e9)))
            lo_hit, hi_hit = max(0, i - px(12, s)), min(len(sc), i + px(12, s) + 1)
            if raw_score[i] < TRK_MARKER_MIN or np.count_nonzero(
                    (raw_score[lo_hit:hi_hit] >= 0.5 * raw_score[i])
                    & ok[lo_hit:hi_hit]) < 2:
                return None
            sc = raw_score
        a, b = max(0, i - px(12, s)), min(len(sc), i + px(12, s) + 1)
        seg = sc[a:b]
        wts = np.where(seg >= 0.5 * sc[i], seg, 0.0)
        return lo + a + float(np.arange(len(seg)) @ wts / wts.sum())


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


class ProgressPolarity:
    """Learn whether the left-to-right progress fill is bright or dark per reel.

    Duskwire fills black over a pale empty area; counting bright pixels reports
    remaining progress backwards. A partial box identifies its colours from
    the two ends. Keep that choice when the box becomes completely full/empty.
    """

    def __init__(self):
        self.bright_fill: Optional[bool] = None

    def fill(self, bright: np.ndarray) -> Optional[float]:
        if self.bright_fill is None:
            n = max(1, min(5, bright.shape[1] // 40))
            left, right = float(bright[:, :n].mean()), float(bright[:, -n:].mean())
            if left >= 0.8 and right <= 0.2:
                self.bright_fill = True
            elif left <= 0.2 and right >= 0.8:
                self.bright_fill = False
            else:
                # A uniform entrance frame does not reveal which colour fills.
                return None
        amount = float(np.median(bright.mean(1)))
        return amount if self.bright_fill else 1.0 - amount


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


PROG_NEAR_MIN_CONTRAST = 40     # box rows vs the rows just outside it (see _box_near)


def _box_near(frame: np.ndarray, top0: int, s: float) -> Optional[tuple[int, int]]:
    """(top, rows) of the progress box within a few px of where it must be, by
    its INSIDE: rows that agree with each other and differ from the row just
    above and just below. Fallback for bright scenes, where the end-border test
    passes on every row. Live (saved_logs/20261003_115620, _115653: Fabulous
    Rod, ~845px-wide window, pale grey background ~190) the box was never
    found in a 12s reel. It was 3-4 rows tall there, not px(11, 0.44) = 5."""
    h, w = frame.shape[:2]
    p0, p1 = _prog_x(w, s)
    c0, c1 = p0 + px(3, s), p1 - px(2, s)
    if c0 < 0 or c1 > w or c1 - c0 < 10:
        return None
    rows_n, d = px(PROG_ROWS, s), px(3, s)
    best, best_sc = None, PROG_NEAR_MIN_CONTRAST
    for top in range(top0 - d, top0 + d + 1):
        for n in range(max(2, min(rows_n - d, int(rows_n * 0.6))), rows_n + 2):
            if top < 1 or top + n + 1 > h:
                continue
            box = frame[top:top + n, c0:c1].astype(np.int16)
            inside = np.median(box, axis=0)
            above = frame[top - 1, c0:c1].astype(np.int16)
            below = frame[top + n, c0:c1].astype(np.int16)
            contrast = min(np.abs(inside - above).sum(1).mean(),
                           np.abs(inside - below).sum(1).mean())
            spread = np.abs(box - inside).sum(2).mean(1).max()
            sc = contrast - spread
            if sc > best_sc:
                best, best_sc = (top, n), sc
    return best


def find_progress(frame: np.ndarray, y_lo: Optional[int] = None,
                  y_hi: Optional[int] = None,
                  scale: Optional[float] = None,
                  polarity: Optional[ProgressPolarity] = None,
                  expect_top: Optional[int] = None) -> Optional[tuple[float, int]]:
    """(amount 0..1, top row) of the progress box, or None if not readable.

    Pass one ProgressPolarity per reel for actual completion in either skin.
    It waits for a partial box to learn the colours. Without that state this
    reports the legacy bright fraction, used by geometry/presence probes.

    The box is located by its two bright end borders -- a run of ~PROG_ROWS rows
    where both are bright -- so it does not need a track reading. That matters:
    it is what tells the bot the reel is still on when the bar itself is lost.
    `scale` defaults to current_scale().

    `expect_top` (frame row where the box must start: known from the track or
    the last sighting) enables _box_near when the border test finds nothing.
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
    runs = []
    if rows.size:
        runs = _runs(np.isin(np.arange(y_hi - y_lo), rows), 1, min_len=rows_n - tol)
        runs = [r for r in runs if r[1] - r[0] + 1 <= rows_n + tol]
    p0, p1 = _prog_x(w, s)
    i0, i1 = px(3, s), px(2, s)
    if runs:
        a, b = max(runs, key=lambda r: r[1] - r[0])
        top = y_lo + a
        mid = frame[top + i0:top + b - a - i1, p0 + i0:p1 - i1]
    else:
        near = _box_near(frame, expect_top, s) if expect_top is not None else None
        if near is None:
            return None
        top, n = near
        e = 1 if n >= 4 else 0          # skip blended edge rows if there are spare
        mid = frame[top + e:top + n - e, p0 + i0:p1 - i1]
    if mid.size == 0:
        return None
    bright = mid.min(2) >= PROG_BRIGHT_MIN
    if polarity is not None:
        fill = polarity.fill(bright)
        return (fill, top) if fill is not None else None
    # Callers that only test the box's presence need no per-reel state. Preserve
    # the legacy bright fraction for those calls; the driver supplies polarity.
    fill = np.median(bright.sum(1)) / (p1 - p0 - i0 - i1)
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
