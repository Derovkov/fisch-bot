"""Noiseform's own minigame: steer the slider onto the zone the warning names.

From the wiki and the user (2026-10-05):
  * every 3.5s, 50% chance: 3 zones appear on the reel bar, each 12% of it;
  * each zone is hatched and has a white icon at its centre that never changes:
    triangle = the black zone, square = the white zone, circle = the green zone;
  * 3 warnings flash in the middle of the screen with one of those shapes, then
    a beam strikes for 0.15s: the slider over the matching zone gives +20%
    progress (and a 33% chance of the Rhythmic mutation); a wrong zone costs
    -28% control, and 4 wrong ones end the minigame. Missed zones disappear.

Here: find the zones and their shapes on the bar (find_zones), read the shape of
a warning (shape_of / WarningWatch), and say where to steer (Noiseform.aim).
Shapes are told by how wide they are near the top, middle and bottom -- the
same for the small bar icons and the big warning, whatever the skin's colours.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from fischrods import RodProfile
from fischtrack import TrackReading, px

ZONE_FRAC = 0.12            # zone width / track width (wiki)
ZONE_WIDTH = (0.6, 1.5)     # x ZONE_FRAC * track width, as found
HATCH_LAGS = (3, 4, 5)      # rows apart: the diagonal stripes are ~8px a period
HATCH_MIN = 16.0            # mean |row - row lag| inside a zone (track: 3-10, zones 20-60)
ICON_WHITE = 190            # icon pixels: all channels at least this
SHAPES = ("triangle", "square", "circle")


def shape_of(mask: np.ndarray) -> Optional[str]:
    """triangle / square / circle from a filled-ish white shape (True pixels),
    by its width (leftmost to rightmost pixel) near the top, middle and bottom:
    15% / 85% down, a circle is ~71% of its middle width, a rounded square ~90%+,
    a triangle narrow at the top. (At 25% a circle is still 87%: too close.)"""
    rows = np.nonzero(mask.any(1))[0]
    cols = np.nonzero(mask.any(0))[0]
    if len(rows) < 6 or not len(cols):
        return None
    top, bottom = rows[0], rows[-1]
    h = bottom - top + 1
    # a shape, not a line: wide enough for its height and mostly filled
    if cols[-1] - cols[0] + 1 < 0.4 * h or mask.sum() < 0.3 * h * (cols[-1] - cols[0] + 1):
        return None

    def width(f):
        y = top + int(round(f * (h - 1)))
        band = mask[max(top, y - 1):min(bottom, y + 1) + 1]
        cols = np.nonzero(band.any(0))[0]
        return float(cols[-1] - cols[0] + 1) if len(cols) else 0.0

    wt, wm, wb = width(.15), width(.5), width(.85)
    if wm <= 0 or wb <= 0:
        return None
    if wt < 0.6 * wb:
        return "triangle"                     # narrow top, wide bottom
    if min(wt, wb) >= 0.85 * wm:
        return "square"
    if max(wt, wb) < 0.85 * wm:
        return "circle"                       # widest in the middle
    return None


def find_zones(band: np.ndarray, x0: int, x1: int, ya: int, yb: int, s: float = 1.0,
               slider: Optional[tuple[int, int]] = None) -> list[tuple[int, int, Optional[str]]]:
    """Zones on the track: [(left, right, shape)] in frame x. `band` rows ya:yb
    are the track's inside, columns x0..x1 the track.

    Found by their ICONS: compact white shapes about the track's height (a
    thin fishing line across one is opened away first). Hatching alone missed
    the black zone on Noiseform's red skin (5-12 there, zones 20-60 elsewhere)
    and read the white fishing line as a zone. The slider's own arrow icons are
    skipped: an icon inside the slider counts only over strong hatching."""
    import cv2

    rows_n = yb - ya
    pad = px(6, s)
    top = max(0, ya - pad)
    I = band[top:yb + pad, x0:x1 + 1]
    W = I.shape[1]
    if rows_n < 8 or W < 50:
        return []
    T = band[ya:yb, x0:x1 + 1].astype(np.float32).mean(2)
    hatch = np.max([np.abs(T[g:] - T[:-g]).mean(0) for g in HATCH_LAGS], axis=0)
    raw = (I.min(2) >= ICON_WHITE).astype(np.uint8)
    # the dark "!" inside the triangle splits it in two: close that 1-2px gap
    raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8))
    # opened to cut a fishing line off an icon (for finding it); the shape is
    # judged on the raw pixels -- opening rounds a small triangle into a blob
    white = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    half = ZONE_FRAC * W / 2
    zones = []
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if not (0.4 * rows_n <= h <= 1.2 * rows_n and 0.5 * h <= w <= 1.8 * h
                and area >= 0.35 * w * h):
            continue
        c = x + w / 2
        if slider is not None and slider[0] - x0 <= c <= slider[1] - x0:
            around = np.r_[max(0, int(c - half)):max(0, int(x)), min(W, int(x + w)):min(W, int(c + half))]
            if not len(around) or hatch[around].mean() < HATCH_MIN:
                continue                                 # one of the slider's arrows
        # all rows of the strip: the opening above trims a triangle's thin tip,
        # and judged from the trimmed box it no longer narrows to a point
        # (live 18:44: a triangle zone read "?" a whole round)
        xs = slice(max(0, x - 1), x + w + 1)
        zones.append((x0 + int(c - half), x0 + int(c + half), shape_of(raw[:, xs] > 0)))
    return sorted(zones)


# --- the warning in the middle of the screen -----------------------------------
# From the user's screen recording (2026-10-05, 1920x1080, 60fps): the warning is
# a big copy of the zone's icon, centred on the screen (966,546), the same place
# and size every time -- a strong-green circle (362px across), a near-black
# triangle (fill 323x288) or a light grey-white square (~360px, 2nd recording),
# each with "!" -- flashing 3 times ~0.3s apart. The
# zones appear on the bar ~0.3s AFTER the last flash; the beam ~2.2s later.
# (Earlier tries looked for "brighter than before": the black triangle is
# darker, and the character's animation and particles change all the time.)
# Checked on every 3rd frame of the recording: each flash read, nothing else.
WARN_HALF = 0.22             # the box read: centre +- this x client height
WARN_SIZE = 200              # ... scaled to this many px square
WARN_MIN_H = 0.36            # the shape's height, x the box (circle 0.41, triangle 0.33*1.1)
WARN_OFF_CENTRE = 0.125      # its centre within this x the box from the box centre
WARN_KINDS = (               # (shape, which pixels are its fill)
    ("circle", lambda R, G, B: G - np.maximum(R, B) > 70),          # strong green
    ("triangle", lambda R, G, B: np.maximum(np.maximum(R, G), B) < 45),  # near black
    # light grey-white (2nd recording, 18:43: fill ~(213,219,229), ~360px square;
    # the daytime sky there was ~(140,180,215) -- not this light)
    ("square", lambda R, G, B: (np.minimum(np.minimum(R, G), B) > 200)
     & (np.maximum(np.maximum(R, G), B) - np.minimum(np.minimum(R, G), B) < 45)),
)
SQUARE_BY_ELIMINATION_S = 1.5   # zones up with no circle/triangle this long before: square
WARN_HOLD_S = 4.0            # aim at the zone this long after the warning (beam ~2.5s on)
STEER = True


def warn_box(h: int, w: int) -> tuple[int, int, int, int]:
    """x0, y0, x1, y1 of the box around the client's centre."""
    half = int(WARN_HALF * h)
    return w // 2 - half, h // 2 - half, w // 2 + half, h // 2 + half


def read_warning(box: np.ndarray) -> Optional[str]:
    """circle / triangle / square if that warning is on screen in `box` (RGB),
    else None. (A missed square is still caught by elimination: Noiseform.zones_up.)"""
    import cv2

    small = cv2.resize(box, (WARN_SIZE, WARN_SIZE), interpolation=cv2.INTER_AREA).astype(np.int16)
    R, G, B = small[..., 0], small[..., 1], small[..., 2]
    c = WARN_SIZE / 2
    for shape, fill in WARN_KINDS:
        m = cv2.morphologyEx(fill(R, G, B).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n < 2:
            continue
        i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        x, y, bw, bh, _ = st[i]
        if (bh < WARN_MIN_H * WARN_SIZE or abs(x + bw / 2 - c) > WARN_OFF_CENTRE * WARN_SIZE
                or abs(y + bh / 2 - c) > WARN_OFF_CENTRE * WARN_SIZE):
            continue
        cnt, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL,
                                  cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros(m.shape, np.uint8)
        cv2.fillPoly(filled, cnt, 1)
        if shape_of(filled[y:y + bh, x:x + bw] > 0) == shape:
            return shape
    return None


class Noiseform(RodProfile):
    """Steers to the zone the warning names; otherwise the normal reel."""
    name = "Noiseform"
    wiki = "https://fischipedia.org/wiki/Noiseform"
    has_extra = False           # no separate minigame: the reel keeps going
    ZONE_SAME_PX = 25           # a zone this close to last frame's is the same zone
    ZONE_FORGET_S = 0.5
    ZONE_COVERED_S = 3.0        # ... kept this long while the slider is over it
    ZONE_CONFIRM = 3            # sightings before a zone counts (stray reads after a beam)

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        # zones seen: [left, right, {shape: votes}, last seen, sightings]
        self._zones: list[list] = []
        self.last_warning: Optional[tuple[str, float]] = None   # (shape, when)
        self.want: Optional[str] = None      # the zone to steer to
        self.want_until = 0.0
        self.guessed = False                 # want came from elimination

    @property
    def zones(self) -> list[tuple[int, int, Optional[str]]]:
        """Zones on the bar now, each with its most-voted shape; if exactly one
        is unknown and the others are two different shapes, it is the third."""
        out = [(z[0], z[1], max(z[2], key=z[2].get) if z[2] else None)
               for z in self._zones if z[4] >= self.ZONE_CONFIRM]
        known = {sh for _, _, sh in out if sh}
        unknown = [i for i, z in enumerate(out) if z[2] is None]
        if len(unknown) == 1 and len(known) == 2 and len(out) == 3:
            (rest,) = set(SHAPES) - known
            i = unknown[0]
            out[i] = (out[i][0], out[i][1], rest)
        return out

    def see_bar(self, frame: np.ndarray, y_off: int, r: TrackReading, now: float) -> None:
        """Each reel frame: where the zones are now (voting on their shapes)."""
        s = r.scale
        had = len(self.zones) >= 2
        sl = (r.slider_x0, r.slider_x1) if r.slider_x0 is not None else None
        covered = (lambda a, b: sl is not None and a < sl[1] and b > sl[0])
        found = find_zones(frame, r.track_x0, r.track_x1,
                           r.y0 + px(4, s) - y_off, r.y1 - px(3, s) + 1 - y_off, s,
                           slider=(r.slider_x0, r.slider_x1) if r.slider_x0 is not None else None)
        for a, b, shape in found:
            z = next((z for z in self._zones if abs((z[0] + z[1]) / 2 - (a + b) / 2)
                      <= px(self.ZONE_SAME_PX, s)), None)
            if z is None:
                z = [a, b, {}, now, 0]
                self._zones.append(z)
            z[0], z[1], z[3] = a, b, now
            z[4] += 1
            # Live (18:44): steering onto the circle zone put the slider over its
            # icon, which then read "square" -- those votes won, the circle zone
            # vanished and the bot went back to the fish. Covered: no vote.
            if shape and not covered(a, b):
                z[2][shape] = z[2].get(shape, 0) + 1
        self._zones = [z for z in self._zones if now - z[3] <= self.ZONE_FORGET_S
                       or (covered(z[0], z[1]) and now - z[3] <= self.ZONE_COVERED_S)]
        if len(self.zones) >= 2 and not had:
            self.zones_up(now)

    def zones_up(self, now: float) -> None:
        """Zones just appeared: the warning before them says which to reach. No
        circle or triangle just before: the (unread) light square."""
        w = self.last_warning
        if w is not None and now - w[1] <= SQUARE_BY_ELIMINATION_S:
            self.want, self.guessed = w[0], False
        else:
            self.want, self.guessed = "square", True
        self.want_until = now + WARN_HOLD_S

    def see_centre(self, box: np.ndarray, now: float) -> Optional[str]:
        """The centre box (warn_box): remembers a circle / triangle warning."""
        shape = read_warning(box)
        if shape is not None:
            self.last_warning = (shape, now)
            if self._zones:                   # zones already up: take it now
                self.want, self.guessed, self.want_until = shape, False, now + WARN_HOLD_S
        return shape

    def aim(self, now: float) -> Optional[float]:
        """Where to steer instead of the fish (frame x), or None."""
        if not STEER:
            return None
        if self.want is None or now > self.want_until or not self.zones:
            self.want = None
            return None
        hit = [z for z in self.zones if z[2] == self.want]
        return (hit[0][0] + hit[0][1]) / 2 if hit else None


from fischrods import SPECIAL  # noqa: E402

SPECIAL["Noiseform"] = Noiseform()
