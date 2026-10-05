"""BarFollower on a synthetic skinned bar: busy static background, a dark slider
with arrows that moves under hold/release physics, a shape reader that mostly
fails and sometimes reads nonsense."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev_tests"))
from fischfollow import BarFollower, SliderModel  # noqa: E402
from fischtrack import TrackReading  # noqa: E402

X0, X1, Y0, Y1, W, SW = 100, 879, 60, 90, 780, 200


def background(seed=1):
    rng = np.random.default_rng(seed)
    bg = rng.integers(40, 220, (150, 1000, 3)).astype(np.uint8)
    bg[:, ::7] = 255 - bg[:, ::7]                     # stripes: lots of fake edges
    return bg


def draw(bg, c, fish):
    f = bg.copy()
    a = int(round(X0 + c - SW / 2))
    f[Y0 + 3:Y1 - 2, a:a + SW] = (12, 12, 14)
    f[Y0 + 3:Y0 + 5, a:a + SW] = f[Y1 - 4:Y1 - 2, a:a + SW] = (200, 200, 205)   # rim
    for k in range(8):                                # arrows near both ends
        f[Y0 + 10 + k:Y0 + 12 + k, a + 15 + k:a + 17 + k] = 230
        f[Y0 + 10 + k:Y0 + 12 + k, a + SW - 17 - k:a + SW - 15 - k] = 230
    x = int(X0 + fish)
    f[Y0 - 12:Y1 + 12, x - 3:x + 3] = (190, 190, 200)  # pill-shaped fish
    return f


def reading(c, fish):
    a = int(round(X0 + c - SW / 2))
    return TrackReading(X0, X1, Y0, Y1, a, a + SW - 1, X0 + fish, scale=1.0, method="tracker")


class FollowTests(unittest.TestCase):
    def run_reel(self, reader_works, seconds=6.0, fps=40, fish_works=0.5, seed=3):
        rng = np.random.default_rng(seed)
        bg = background()
        c, v, fish, t = W / 2, 0.0, W / 2, 0.0
        f0 = draw(bg, c, fish)
        follow = BarFollower(f0, reading(c, fish), 0, now=0.0,
                             find_fish=lambda fr, yo, near, reach:
                             truth[1] if rng.random() < fish_works else None)
        errs, outs = [], 0
        for i in range(1, int(seconds * fps)):
            dt = 1 / fps
            t += dt
            held = (int(t / 0.35) % 2) == 0
            v += (900 if held else -900) * dt
            c += v * dt
            if c < SW / 2 or c > W - SW / 2:
                c, v = min(max(c, SW / 2), W - SW / 2), 0.0
            fish = W / 2 + 250 * np.sin(t * 1.3)
            truth = (c, fish)
            frame = draw(bg, c, fish)
            u = rng.random()
            r = (reading(c, fish) if u < reader_works
                 else reading(rng.uniform(SW / 2, W - SW / 2), fish) if u < reader_works + 0.1
                 else None)
            out = follow.update(frame, 0, t, held, r)
            if out is not None:
                outs += 1
                errs.append(abs(out.slider_centre - X0 - c))
        return np.array(errs), outs / (int(seconds * fps) - 1), follow

    def test_follows_when_the_shape_reader_mostly_fails(self):
        errs, cover, follow = self.run_reel(reader_works=0.15)
        self.assertGreater(cover, 0.97)
        self.assertLess(np.percentile(errs, 95), 12)
        self.assertGreater(follow.model.acc[True], 0)        # learned: holding pushes right
        self.assertLess(follow.model.acc[False], 0)

    def test_agrees_with_a_good_reader(self):
        errs, cover, _ = self.run_reel(reader_works=0.9)
        self.assertEqual(cover, 1.0)
        self.assertLess(np.percentile(errs, 95), 6)

    def test_gives_up_when_the_bar_is_gone(self):
        bg = background()
        follow = BarFollower(draw(bg, W / 2, W / 2), reading(W / 2, W / 2), 0, now=0.0)
        out = None
        for i in range(1, 40):
            out = follow.update(bg, 0, i / 40, False, None)
        self.assertIsNone(out)
        self.assertIn("not seen", follow.why)

    def test_model_stops_at_the_track_ends(self):
        m = SliderModel(100, 100, 680)
        c, v = m.predict(0.5, False)
        self.assertEqual((c, v), (100, 0.0))



class RecordingTests(unittest.TestCase):
    def test_bot_recording_replays_offline(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import Mock
        from fischbot import FischBot, MacroConfig
        import replay_skin

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        b = FischBot.__new__(FischBot)
        b.cfg = MacroConfig(trace=True)
        b.session = SimpleNamespace(path=lambda n: Path(tmp.name) / n)
        b.mouse = SimpleNamespace(down=False)
        b.rod, b.enchants, b.log = SimpleNamespace(name="Fabulous Rod"), ["Crested"], Mock()
        b._rec, b._rec_n = [], 0
        bg = background()
        for i in range(60):
            c = W / 2 + 150 * np.sin(i / 10)
            b.mouse.down = i % 20 < 10
            b._record_bar(draw(bg, c, W / 2), 0, i / 40, reading(c, W / 2))
        b._save_recording()
        self.assertTrue((Path(tmp.name) / "rec_01.npz").exists())
        frames, rod = replay_skin.load_recording(Path(tmp.name) / "rec_01.npz")
        self.assertEqual((len(frames), rod), (60, "Fabulous Rod"))
        self.assertEqual(frames[0][4].slider_width, SW)
        alone, both = replay_skin.replay(frames)
        self.assertGreater(sum(r is not None for r in both), 50)
        b._record_bar(draw(bg, W / 2, W / 2), 0, 0, reading(W / 2, W / 2))   # 2 reels max ...
        b._rec_n = 2
        b._rec = []
        b._record_bar(draw(bg, W / 2, W / 2), 0, 0, reading(W / 2, W / 2))
        self.assertEqual(b._rec, [])                                          # ... then none



class UnevenTrackTests(unittest.TestCase):
    """Noiseform's default bar: claw art over the track (2026-10-05)."""

    def test_art_covered_track_reads_only_once_the_scene_is_clear(self):
        import cv2
        import fischtrack as ft
        p = ROOT / "dev_tests" / "fixtures" / "bar_noiseform_default.png"
        if not p.exists():
            self.skipTest("fixture not present (git-ignored)")
        ft.set_client(1920, 1009)
        im = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        fr = np.full((1009, 1920, 3), 120, np.uint8)
        fr[800:800 + im.shape[0], 511:511 + im.shape[1]] = im
        try:
            ft.set_scene_clear(False)
            self.assertIsNone(ft.read_track(fr))           # catch message may be up
            ft.set_scene_clear(True)
            r = ft.read_track(fr)
            self.assertIsNotNone(r)
            self.assertEqual((r.track_x0, r.track_x1), (571, 1348))
        finally:
            ft.set_scene_clear(False)


if __name__ == "__main__":
    unittest.main()
