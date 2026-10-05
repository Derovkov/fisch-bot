"""Replay a recorded reel bar (rec_NN.npz, saved with "Record measurements" on)
through the skin readers, offline.

    python dev_tests/replay_skin.py saved_logs/<run>/rec_01.npz [--sheet out.png]

Prints, per reader, how many frames it read and where it put the slider, and
optionally writes a contact sheet (every few frames, boxes drawn):
    yellow = what the bot read live, red = SkinTracker alone,
    green = SkinTracker + BarFollower (fischfollow).
"""
import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fischfollow import BarFollower  # noqa: E402
from fischtrack import SkinTracker, TrackReading  # noqa: E402


def load_recording(path):
    """[(t, canvas RGB in client x coords, y_off, held, live reading)], rod."""
    z = np.load(path, allow_pickle=True)
    out = []
    for t, y, x, held, jpg, rd in zip(z["t"], z["y"], z["x"], z["held"], z["jpg"], z["reading"]):
        img = cv2.cvtColor(cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR),
                           cv2.COLOR_BGR2RGB)
        canvas = np.zeros((img.shape[0], int(x) + img.shape[1], 3), np.uint8)
        canvas[:, int(x):] = img
        v = json.loads(str(rd))
        live = TrackReading(*v[:7], scale=v[7], method=v[8])
        out.append((float(t), canvas, int(y), bool(held), live))
    return out, str(z["rod"])


def replay(frames):
    """Readings per frame: (tracker alone, tracker + follower)."""
    t0, f0, y0, _, live0 = frames[0]
    tracker = SkinTracker(f0, live0, y0, now=t0)
    tracker2 = SkinTracker(f0, live0, y0, now=t0)
    follow = BarFollower(f0, tracker2.reading(), y0, now=t0, find_fish=tracker2._find_marker)
    alone, both = [], []
    for t, f, y, held, _ in frames[1:]:
        alone.append(tracker.read(f, y, now=t))
        both.append(follow.update(f, y, t, held, tracker2.read(f, y, now=t)))
    return alone, both


def summary(name, rs, live):
    got = [(r, l) for r, l in zip(rs, live) if r is not None and r.slider_x0 is not None]
    diffs = [abs(r.slider_centre - l.slider_centre) for r, l in got if l.slider_x0 is not None]
    inside = sum(r.fish_inside for r, _ in got)
    print(f"{name:22s} read {len(got)}/{len(rs)} frames ({len(got) / max(1, len(rs)):.0%}), "
          f"fish inside {inside}/{len(got)}"
          + (f", vs live: median {np.median(diffs):.0f}px, p90 {np.percentile(diffs, 90):.0f}px"
             if diffs else ""))


def sheet(frames, alone, both, path, every=8):
    rows = []
    for i in range(0, len(alone), every):
        t, f, y, held, live = frames[i + 1]
        o = cv2.cvtColor(f, cv2.COLOR_RGB2BGR)
        x0 = live.track_x0 - 60
        o = o[:, max(0, x0):].copy()
        for r, col, dy in ((live, (0, 255, 255), 0), (alone[i], (0, 0, 255), 3), (both[i], (0, 255, 0), 6)):
            if r is not None and r.slider_x0 is not None:
                cv2.rectangle(o, (r.slider_x0 - max(0, x0), r.y0 - y + dy),
                              (r.slider_x1 - max(0, x0), r.y1 - y - dy), col, 2)
                if r.marker_x is not None:
                    mx = int(r.marker_x) - max(0, x0)
                    cv2.line(o, (mx, r.y0 - y - 8 - dy), (mx, r.y0 - y - 2 - dy), col, 2)
        cv2.putText(o, f"t={t:.2f}s {'HELD' if held else ''}", (4, o.shape[0] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)
        rows.append(o)
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0))) for r in rows]
    cv2.imwrite(path, np.vstack(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--sheet")
    a = ap.parse_args()
    frames, rod = load_recording(a.recording)
    dur = frames[-1][0] - frames[0][0]
    print(f"{rod}: {len(frames)} frames over {dur:.1f}s ({len(frames) / max(dur, 1e-3):.0f}/s)")
    alone, both = replay(frames)
    live = [fr[4] for fr in frames[1:]]
    summary("live (as recorded)", live, live)
    summary("SkinTracker alone", alone, live)
    summary("tracker + follower", both, live)
    if a.sheet:
        sheet(frames, alone, both, a.sheet)
        print("sheet:", a.sheet)


if __name__ == "__main__":
    main()
