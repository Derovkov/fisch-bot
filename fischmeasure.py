"""Measure the reel bar against its background -- numbers only, no images saved.

    python fischmeasure.py [seconds]

Sends no input. Start it, then cast and reel by hand. Every ~0.1s it records,
as plain numbers in measure_logs/measure_<time>.txt:

  * how far fischtrack.read_track got (which stage rejected the frame), and
  * colour samples at the bar's KNOWN screen position: the bar is screen-space UI,
    so its x-range is fixed (571-1348 at 1920 wide on both live spots and the
    recording; other client sizes scale it, see fischtrack's UI scale). Its rows
    are found by geometry -- the rows where that x-range is darker than the
    background just left/right of it -- not by colour thresholds, so this works
    even where fischtrack's thresholds fail.

These are the inputs the per-cast calibration needs. Nothing but colour
statistics is written to disk.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

import fischtrack as ft
from fastcap import FastGrabber, find_roblox_window

# Bar geometry in px at UI scale 1.0 (1920 wide); the track's ends come from
# fischtrack.track_x.
FLANK_W = 90                      # background strips each side of the track
FLANK_GAP = 18                    # skip the end-cap triangles
TRACK_ROWS = ft.GEO_ROWS


def locate_rows(frame: np.ndarray, s: Optional[float] = None) -> tuple[int, float]:
    """(y0, score): the track-height row window with the strongest edges at the
    track's two ends (fischtrack.edge_scores). `s` defaults to the current scale."""
    s = ft.current_scale() if s is None else s
    h = frame.shape[0]
    y_lo = ft.search_top(h, s)
    rows = ft.px(TRACK_ROWS, s)
    win = np.convolve(ft.edge_scores(frame, y_lo, h, s), np.ones(rows) / rows,
                      mode="valid")
    k = int(np.argmax(win))
    return y_lo + k, float(win[k])


def explain(frame: np.ndarray) -> str:
    """Bot reader (geometry) result, plus which stage the OLD colour reader
    (read_track_color) reaches -- kept so logs stay comparable."""
    r = ft.read_track(frame)
    geo = ("geo=HIT scale=%.2f slider=%d-%d fish=%.0f"
           % (r.scale, r.slider_x0, r.slider_x1, r.marker_x) if r else "geo=miss")
    return geo + " | colour: " + _explain_color(frame)


def _explain_color(frame: np.ndarray) -> str:
    h, w = frame.shape[:2]
    y_lo, y_hi = int(h * ft.SEARCH_TOP_FRAC), int(h * ft.SEARCH_BOTTOM_FRAC)
    track, slider = ft._masks(frame, y_lo, y_hi, 0, w)
    lo, hi = int(w * ft.BAR_MIN_SPAN_FRAC), int(w * ft.BAR_MAX_SPAN_FRAC)
    counts = (track | slider).sum(1)
    pre = np.flatnonzero(counts >= lo * 0.6)
    good = 0
    spans = []
    for i in pre:
        rb = ft._row_bar(track[i], slider[i])
        if rb is None:
            continue
        x0, x1, s0, s1 = rb
        spans.append(x1 - x0)
        if lo <= x1 - x0 <= hi and s1 - s0 + 1 >= ft.SLIDER_MIN_WIDTH_FRAC * (x1 - x0):
            good += 1
    r = ft.read_track_color(frame)
    med_span = int(np.median(spans)) if spans else 0
    return (f"pre={len(pre)} rowbar={len(spans)} medspan={med_span} good={good} "
            f"read={'HIT' if r else 'miss'}")


def sample(frame: np.ndarray, y0: int, s: Optional[float] = None) -> str:
    """Median RGB of the regions that calibration would use."""
    s = ft.current_scale() if s is None else s
    h, w = frame.shape[:2]
    x0, x1 = ft.track_x(w, s)
    fw, fg = ft.px(FLANK_W, s), ft.px(FLANK_GAP, s)
    rows = ft.px(TRACK_ROWS, s)
    ym = slice(y0 + ft.px(8, s), y0 + rows - ft.px(8, s))   # middle rows of the track

    def p(n: int) -> int:
        return ft.px(n, s)

    def med(ys, xs) -> str:
        px = frame[ys, xs].reshape(-1, 3)
        if px.size == 0:              # region off the frame (rows near the bottom)
            return "(n/a)"
        return "(%d,%d,%d)" % tuple(np.median(px, axis=0))

    cx = (x0 + x1) // 2
    # The per-row dark/bright levels read_track splits the slider at -- if they
    # are close (bright background through the track) the split fails.
    sums = frame[min(h - 1, y0 + rows // 2), x0:x1 + 1].astype(np.int16).sum(1)
    lo, hi = (np.percentile(sums, [15, 97]) if sums.size else (0, 0))
    return f"split=({lo:.0f},{hi:.0f}) " + " ".join([
        f"trackL={med(ym, slice(x0 + p(20), x0 + p(220)))}",
        f"trackR={med(ym, slice(x1 - p(220), x1 - p(20)))}",
        f"flankL={med(ym, slice(max(0, x0 - fg - fw), x0 - fg))}",
        f"flankR={med(ym, slice(x1 + fg, x1 + fg + fw))}",
        f"above={med(slice(max(0, y0 - p(40)), max(0, y0 - p(25))), slice(x0, x1))}",
        f"below={med(slice(y0 + rows + p(25), min(h, y0 + rows + p(40))), slice(x0, x1))}",
        f"centre={med(ym, slice(cx - p(110), cx - p(15)))}",     # slider at reel start
        f"marker={med(slice(max(0, y0 - p(12)), y0 + rows + p(10)), slice(cx - 3, cx + 4))}",
    ])


def main() -> None:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    win = find_roblox_window()
    if win is None:
        raise SystemExit("No Roblox window found.")
    _, rect, _ = win
    ft.set_client(rect.width, rect.height)
    g = FastGrabber(rect)
    out = Path("measure_logs")
    out.mkdir(exist_ok=True)
    path = out / f"measure_{datetime.now():%Y%m%d_%H%M%S}.txt"
    print(f"measuring {secs:.0f}s ({rect.width}x{rect.height}, UI scales "
          f"{', '.join(f'{s:.2f}' for s in ft.scales())}) -> {path}")
    print("no input is sent: cast and reel one fish by hand now.")
    hits = n = 0
    t_end = time.perf_counter() + secs
    t0 = time.perf_counter()
    with path.open("w", encoding="utf-8") as f:
        f.write(f"client {rect.width}x{rect.height}\n")
        while time.perf_counter() < t_end:
            t = time.perf_counter()
            frame = g.grab()
            s = ft.current_scale()
            y0, score = locate_rows(frame, s)
            ex = explain(frame)
            hits += "geo=HIT" in ex
            line = (f"t={t - t0:6.2f} rows={y0}-{y0 + ft.px(TRACK_ROWS, s) - 1} "
                    f"darkness={score:5.0f} {ex} {sample(frame, y0, s)}")
            f.write(line + "\n")
            n += 1
            time.sleep(max(0.0, 0.1 - (time.perf_counter() - t)))
    g.close()
    print(f"{n} samples, {hits} read by the current detector. Log: {path}")


if __name__ == "__main__":
    main()
