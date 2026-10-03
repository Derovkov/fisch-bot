"""
Reel-bar skins: recognise which skin a reel uses and reuse what was learned.

The user (2026-10-03): "I was using a skin for the Fabulous Rod, we need some
way to be able to differentiate and respond to skins." Skins change more than
colours -- the pastel skin's track is 93% of the normal length (x 601-1324 at
1920 wide) under a bright art banner, its progress bar fills pink-white-yellow
over a muted pink empty part, and its slider is a gradient capsule.

A skin's fingerprint, taken from the reel's first readings:
    k         track length / the default length (1.0 = normal)
    track     median colour of the track outside the slider
    slider    median colour inside the slider
    progress  how the progress box fills: bright / dark / step (ProgressPolarity)
Fingerprints are matched to saved skins (`skins` in fischbot_general.json --
general config, not part of saved setups). Per rod (and enchants) a skin also
keeps the slider width and how its reels went.

Responding: at the start of a run the rod's last skin primes the track search
with its length (fischtrack.set_skin_track_k) and the bot's slider width
(fischbot.slider_w); each reel's recognised skin is logged. New skins are only
saved from readable reels, named "Skin N" -- the user can rename or forget
them in Settings.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

K_TOL = 0.02            # track lengths this close are the same skin
TRACK_TOL = 70          # channel-sum distance of the track colours
SLIDER_TOL = 130        # ... of the slider colours (gradients, icons: looser)


def fingerprint(frame: np.ndarray, r, y_off: int = 0) -> Optional[dict]:
    """Fingerprint of the bar in `frame` from reading `r` (TrackReading), or
    None if the reading has no slider."""
    from fischtrack import TRACK_HALF_W, px

    if r is None or r.slider_x0 is None:
        return None
    s = r.scale
    y0, y1 = r.y0 + px(6, s) - y_off, r.y1 - px(5, s) + 1 - y_off
    band = frame[max(0, y0):max(0, y1), r.track_x0:r.track_x1 + 1].astype(np.float32)
    if band.shape[0] < 2 or band.shape[1] < 20:
        return None
    P = np.median(band, axis=0)
    a, b = r.slider_x0 - r.track_x0, r.slider_x1 - r.track_x0 + 1
    m = max(2, px(6, s))
    inside = P[max(0, a + m):max(0, b - m)]
    keep = np.ones(len(P), bool)
    keep[max(0, a - m):min(len(P), b + m)] = False
    if r.marker_x is not None:
        mx = int(round(r.marker_x - r.track_x0))
        keep[max(0, mx - px(12, s)):max(0, mx + px(12, s) + 1)] = False
    outside = P[keep]
    if len(inside) < 3 or len(outside) < 10:
        return None
    return {"k": round((r.track_x1 - r.track_x0) / (2 * TRACK_HALF_W * s), 3),
            "track": [int(v) for v in np.median(outside, 0)],
            "slider": [int(v) for v in np.median(inside, 0)],
            "method": r.method}


def _dist(a, b) -> float:
    return float(sum(abs(x - y) for x, y in zip(a, b)))


def same_skin(fp: dict, skin: dict) -> bool:
    return (abs(fp["k"] - skin["k"]) <= K_TOL
            and _dist(fp["track"], skin["track"]) <= TRACK_TOL
            and _dist(fp["slider"], skin["slider"]) <= SLIDER_TOL)


def rod_key(rod: str, enchants) -> str:
    return rod + (" | " + ", ".join(sorted(enchants)) if enchants else "")


def clean_skins(raw) -> list[dict]:
    out = []
    for s in raw if isinstance(raw, list) else []:
        try:
            out.append({"id": str(s["id"]), "name": str(s.get("name") or s["id"])[:40],
                        "k": float(s["k"]), "track": [int(v) for v in s["track"]][:3],
                        "slider": [int(v) for v in s["slider"]][:3],
                        "progress": s.get("progress") if s.get("progress") in ("bright", "dark", "step") else None,
                        "method": s.get("method") if s.get("method") in ("colour", "geo", "tracker") else None,
                        "rods": {str(k): {"slider_w": (float(v["slider_w"]) if v.get("slider_w") else None),
                                          "reels": int(v.get("reels", 0)),
                                          "caught": int(v.get("caught", 0)),
                                          "perfect": int(v.get("perfect", 0))}
                                 for k, v in (s.get("rods") or {}).items() if isinstance(v, dict)},
                        "last_rod": s.get("last_rod"), "last_seen": float(s.get("last_seen", 0))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


class SkinBook:
    """The saved skins; one per bot run. `save` persists (general config)."""

    def __init__(self, skins: list[dict], log, save=None, clock=time.time):
        self.skins = clean_skins(skins)
        self.log = log
        self._save = save
        self.clock = clock
        self.current: Optional[dict] = None       # this reel's skin
        self._pending: Optional[dict] = None      # this reel's fingerprint
        self._unknown: Optional[dict] = None      # last unrecognised one (log once)

    def last_for(self, rod: str) -> Optional[dict]:
        """The skin this rod used most recently."""
        mine = [s for s in self.skins if s.get("last_rod") == rod]
        return max(mine, key=lambda s: s["last_seen"]) if mine else None

    def prime(self, rod: str, enchants) -> Optional[float]:
        """Run start / rod change: point the track search at the rod's last
        skin. Returns that skin's slider width for these enchants, if known."""
        from fischtrack import set_skin_track_k

        s = self.last_for(rod)
        set_skin_track_k(s["k"] if s and abs(s["k"] - 1.0) > 0.004 else None)
        if s is None:
            return None
        self.log(f"skin: {rod} last used '{s['name']}'"
                 + (f" (track {s['k']:.0%} long)" if abs(s["k"] - 1.0) > 0.004 else ""))
        w = s["rods"].get(rod_key(rod, enchants), {}).get("slider_w")
        return w

    def match(self, fp: dict) -> Optional[dict]:
        hits = [s for s in self.skins if same_skin(fp, s)]
        return min(hits, key=lambda s: abs(fp["k"] - s["k"]) * 1000 + _dist(fp["slider"], s["slider"])) \
            if hits else None

    def begin(self, fp: Optional[dict], rod: str) -> Optional[dict]:
        """A reel started with fingerprint `fp`: recognise its skin."""
        self._pending = fp
        prev = self.current
        self.current = self.match(fp) if fp else None
        if self.current is not None and self.current is not prev:
            self.log(f"skin: '{self.current['name']}' recognised")
        elif fp is not None and self.current is None and not (
                self._unknown is not None and same_skin(fp, self._unknown)):
            self._unknown = fp
            self.log(f"skin: not one seen before (track {fp['k']:.0%} long, track "
                     f"rgb{tuple(fp['track'])}, slider rgb{tuple(fp['slider'])}) -- "
                     f"saved after a readable reel")
        return self.current

    def end(self, rod: str, enchants, readable: bool, caught: bool, perfect: Optional[bool],
            progress: Optional[str], slider_w: Optional[float]) -> None:
        """The reel ended: update the skin's stats; save a new skin only from a
        readable reel (progress box read and the bar followed well)."""
        fp, s = self._pending, self.current
        self._pending = None
        if fp is None:
            return
        if s is None:
            if not readable:
                return
            n = 1 + max([int(x["id"][1:]) for x in self.skins if x["id"][1:].isdigit()] or [0])
            s = {"id": f"s{n}", "name": f"Skin {n}", "k": fp["k"], "track": fp["track"],
                 "slider": fp["slider"], "progress": progress, "method": fp["method"],
                 "rods": {}, "last_rod": rod, "last_seen": self.clock()}
            self.skins.append(s)
            self.current = s
            self.log(f"skin: saved as '{s['name']}' -- rename it in Settings > Reel skins")
        elif readable:
            # Drift slowly toward what is seen (lighting differs by area).
            for key in ("track", "slider"):
                s[key] = [int(round(0.8 * a + 0.2 * b)) for a, b in zip(s[key], fp[key])]
            s["progress"] = progress or s.get("progress")
        st = s["rods"].setdefault(rod_key(rod, enchants), {"slider_w": None, "reels": 0,
                                                          "caught": 0, "perfect": 0})
        st["reels"] += 1
        st["caught"] += int(bool(caught))
        st["perfect"] += int(bool(perfect))
        if slider_w and readable:
            st["slider_w"] = round(float(slider_w), 1)
        s["last_rod"], s["last_seen"] = rod, self.clock()
        if self._save is not None:
            try:
                self._save(self.skins)
            except OSError as exc:
                self.log(f"skin: could not save ({exc})")
