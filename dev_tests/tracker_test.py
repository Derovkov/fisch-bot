"""fischtrack.SkinTracker over each run's crops, in crop coordinates (scale 1).
Truth (eyeballed from the images, crop x): slider a-b, fish."""
import glob
import sys
import time

import cv2
import numpy as np

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fischtrack as ft  # noqa: E402

TRUTH = {
    "090708": {"6.20": (444, 560, 531), "7.23": (115, 230, 596), "9.32": (60, 176, 684)},
    "090725": {"6.28": (191, 307, 448)},
    "090751": {"5.17": (381, 556, 448), "5.79": (316, 490, 506), "6.33": (105, 280, 555),
               "7.71": (60, 236, 632)},
    "090805": {"4.96": (360, 534, 448), "5.57": (164, 338, 458), "6.07": (105, 280, 472),
               "6.60": (60, 236, 483)},
    "093715": {"6.73": (298, 471, 641), "19.26": (175, 348, 263)},
    "093738": {"9.83": (240, 413, 337)},
    "093807": {"7.15": (314, 430, 353)},
    "094007": {"6.33": (348, 605, 359), "9.34": (70, 327, 192)},
}
s = 1.0
bad = checked = missed = 0
for d in sys.argv[1:]:
    key = d.rstrip("\\").split("_")[-1]
    print("=====", key)
    tr = None
    for p in sorted(glob.glob(d + r"\bar_*.png")):
        name = p.split("\\")[-1]
        t = float(name[4:11])
        f = cv2.imread(p)[:, :, ::-1].copy()
        h, wd = f.shape[:2]
        # crops are cut px(60) either side of the bot's learned track ends
        ft.lock_scale(s)
        # (090751/090805 were cut around a mis-learned right end, 22px too far
        # out; the real track is 60..837 in every crop)
        ft.set_geometry(track_x_frac=(60 / wd, 837 / wd))
        x0, x1 = ft.track_x(wd, s)
        rows = ft.px(ft.GEO_ROWS, s)
        win = np.convolve(ft.edge_scores(f, 0, h, s), np.ones(rows) / rows, mode="valid")
        y0 = int(np.argmax(win))
        if tr is None:
            r0 = ft.TrackReading(x0, x1, y0, y0 + rows - 1, x0 + 300, x1 - 300, (x0 + x1) / 2)
            tr = ft.SkinTracker(f, r0, 0, now=t)
            # live, the bot reads ~30x/s and the reel's first second is static
            # (slider + fish centred): a few settled reads learn the fish colour
            for k in range(6):
                tr.read(f, 0, now=t + 0.03 * (k + 1))
            t += 0.18
            print(f"  {name}: START slider {tr.x0 + tr.c - tr.w / 2:.0f}-{tr.x0 + tr.c + tr.w / 2:.0f} "
                  f"fish {tr.x0 + tr.m:.0f} rgb {tr.mrgb}")
            continue
        # crops are re-cut around each frame's own row estimate: move the
        # tracker's rows to this crop's (live, rows stay put)
        dy = y0 - tr.y0
        tr.y0 += dy; tr.y1 += dy
        t0 = time.perf_counter()
        r = tr.read(f, 0, now=t)
        ms = (time.perf_counter() - t0) * 1000
        tt = TRUTH.get(key, {}).get(f"{t:.2f}".lstrip("0"))
        if r is None:
            print(f"  {name}: none  ({ms:.1f}ms)" + (f"  TRUTH {tt}" if tt else ""))
            missed += tt is not None
            continue
        line = (f"  {name}: slider {r.slider_x0}-{r.slider_x1} fish {r.marker_x:.0f} "
                f"{'IN ' if r.fish_inside else 'OUT'} ({ms:.1f}ms)")
        if tt:
            checked += 1
            ok = (abs(r.slider_x0 - tt[0]) <= 8 and abs(r.slider_x1 - tt[1]) <= 8
                  and abs(r.marker_x - tt[2]) <= 4)
            bad += not ok
            line += f"  truth {tt[0]}-{tt[1]} fish {tt[2]} {'OK' if ok else 'WRONG'}"
        print(line)
print(f"labelled crops: {checked} read, {missed} rejected; wrong accepted readings: {bad}")
raise SystemExit(1 if bad else 0)
