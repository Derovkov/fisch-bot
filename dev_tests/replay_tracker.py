"""Replay the 2026-10-01 recording through FischBot.reel() at real-time pace,
recoloured into a Duskwire-like skin (slider white -> near-black, pink marker ->
white), so the bot must use the skin tracker. Open loop. Every reading the
controller acts on is compared with the proven colour reader on the ORIGINAL
frame.  usage: replay_tracker.py [skin|plain] [--trace]"""
import os
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fischtrack as ft  # noqa: E402
from fischbot import FischBot, MacroConfig  # noqa: E402
from fischcontrol import ControlConfig  # noqa: E402
from fischsession import Session  # noqa: E402

# The 2026-10-01 recording (frames.npy, not in the repo): set FISCH_FRAMES to its path.
F = np.load(os.environ.get("FISCH_FRAMES", "frames.npy"), mmap_mode="r")
SKIN = "plain" not in sys.argv
CLIENT = slice(23, 1032)        # recording = full desktop; live grabs = 1920x1009 client
F_H, F_W = 1009, F.shape[2]
truth_cache = {}


def truth(i):
    if i not in truth_cache:
        truth_cache[i] = ft.read_track(np.ascontiguousarray(F[i][CLIENT]), scale=1.0)
    return truth_cache[i]


def recolour(i, f):
    rd = truth(i)
    f = np.array(f)
    if rd is None or rd.method != "colour":
        return f
    sl = f[rd.y0:rd.y1 + 1, rd.slider_x0:rd.slider_x1 + 1]
    m = sl.astype(np.int16).min(2) >= 190
    sl[m] = (6, 3, 3)
    mx = int(round(rd.marker_x))
    y0, y1 = rd.y0 - 22, rd.y1 + 18
    col = f[max(0, y0):y1, mx - 9:mx + 10].astype(np.int16)
    r, g, b = col[..., 0], col[..., 1], col[..., 2]
    mk = (r >= 195) & (g >= 160) & (g <= 205) & (b >= 180) & (b <= 228) & (r - g >= 22)
    f[max(0, y0):y1, mx - 9:mx + 10][mk] = (255, 255, 255)
    return f


class Grabber:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.served = []
        self.rect = SimpleNamespace(height=F_H, width=F_W)

    def grab(self):
        i = min(int((time.perf_counter() - self.t0) * 60), len(F) - 1)
        self.served.append(i)
        f = np.ascontiguousarray(F[i][CLIENT])
        return recolour(i, f) if SKIN else f

    def grab_rows(self, y0, y1):
        return np.ascontiguousarray(self.grab()[y0:y1])

    def close(self):
        pass


class Mouse:
    down = False

    def set_down(self, v):
        self.down = v

    def release(self):
        self.down = False


g = Grabber()
ft.set_client(F_W, F_H)
# warm the truth cache so the recolouring does not slow the replay
print("precomputing truth...", flush=True)
for i in range(len(F)):
    truth(i)
g.t0 = time.perf_counter()
rect = SimpleNamespace(width=F_W, height=F_H, left=0, top=0)
cfg = MacroConfig(reel_timeout_s=len(F) / 60, bite_timeout_s=60, trace="--trace" in sys.argv)
sess = Session()
bot = FischBot(cfg, ControlConfig(), g, rect, None, Mouse(),
               SimpleNamespace(ready=lambda: True), session=sess)
seen = []
_step = bot.ctl.step
_grab_and_read = bot._grab_and_read


def grab_and_read(hint, tracker=None):
    reading = _grab_and_read(hint, tracker)
    # OCR can make a separate notification-band grab before ctl.step. Compare
    # geometry to its own source frame, rather than that newer notification.
    bot._replay_read_index = g.served[-1]
    return reading


def step(r, now=None):
    if r is not None:
        seen.append((bot._replay_read_index, r))
    return _step(r, now=now)


bot.ctl.step = step
bot._grab_and_read = grab_and_read
bot.running = True
for _ in range(3):
    if g.served and g.served[-1] >= len(F) - 1:
        break
    # Match the real run's pre-cast caption guard without sending cast input.
    # Calling reel() directly during the preceding catch animation would start
    # a phantom attempt that the live cast() now explicitly waits out.
    if not bot._wait_catch_clear():
        break
    if g.served and g.served[-1] >= len(F) - 1:
        break
    bot.catch_watch.begin_attempt()
    bot.reel()

sl, fi, agree, methods = [], [], [], {}
for i, r in seen:
    t = truth(i)
    methods[r.method] = methods.get(r.method, 0) + 1
    if t is None or t.method != "colour":
        continue
    sl.append(abs(r.slider_centre - t.slider_centre))
    fi.append(abs(r.marker_x - t.marker_x))
    agree.append(r.fish_inside == t.fish_inside)
sl, fi = np.array(sl), np.array(fi)
served = np.array(g.served)
print(f"readings acted on: {len(seen)} by method {methods}; vs truth: slider centre err "
      f"median {np.median(sl):.1f} p95 {np.percentile(sl, 95):.1f} max {sl.max():.0f}; fish "
      f"median {np.median(fi):.1f} p95 {np.percentile(fi, 95):.1f} max {fi.max():.0f}; "
      f"inside agrees {np.mean(agree):.1%}")
print(f"grabs {len(served)}, median {np.median(np.diff(served)) / 60 * 1000:.0f}ms/iteration")
print("reel logs:", *bot.reel_logs, sep="\n  ")
if "--trace" in sys.argv:
    import shutil
    dst = os.path.join(tempfile.gettempdir(), "replay_trace")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(sess.dir, dst)
bot.catch_watch.close()
sess.close() if hasattr(sess, "close") else None
