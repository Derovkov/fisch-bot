"""Noiseform's zone minigame: zone icons, warning shapes, where to steer."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev_tests"))
from fischnoise import Noiseform, find_zones, read_warning, shape_of  # noqa: E402
from fischtrack import TrackReading  # noqa: E402

X0, X1, Y0, Y1 = 100, 919, 40, 70          # track 820 wide, 31 rows


def draw_icon(img, cx, cy, shape, size=16, colour=(255, 255, 255), thick=-1):
    r = size // 2
    if shape == "square":
        cv2.rectangle(img, (cx - r, cy - r), (cx + r, cy + r), colour, thick)
    elif shape == "circle":
        cv2.circle(img, (cx, cy), r, colour, thick)
    else:
        pts = np.array([(cx, cy - r), (cx - r - 2, cy + r), (cx + r + 2, cy + r)])
        cv2.fillPoly(img, [pts], colour) if thick < 0 else cv2.polylines(img, [pts], True, colour, thick)


def bar(zones, slider=(380, 540)):
    """A dark green track with hatched zones (track coords) and their icons."""
    f = np.zeros((110, 1020, 3), np.uint8)
    f[Y0:Y1 + 1, X0:X1 + 1] = (8, 40, 20)
    a, b = slider
    f[Y0 + 3:Y1 - 2, X0 + a:X0 + b] = (40, 180, 100)
    for x, shape, col in zones:
        zx0, zx1 = X0 + x - 49, X0 + x + 49
        for yy in range(Y0 + 3, Y1 - 2):
            for xx in range(zx0, zx1):
                f[yy, xx] = col if ((xx + yy) // 4) % 2 else tuple(c // 3 for c in col)
        draw_icon(f, X0 + x, (Y0 + Y1) // 2, shape)
    return f


def reading(slider=(380, 540)):
    return TrackReading(X0, X1, Y0, Y1, X0 + slider[0], X0 + slider[1], X0 + 460, scale=1.0)


class ShapeTests(unittest.TestCase):
    def test_filled_and_outlined_shapes(self):
        for shape in ("square", "circle", "triangle"):
            for thick in (-1, 3):
                m = np.zeros((60, 60), np.uint8)
                draw_icon(m, 30, 30, shape, size=40, colour=1, thick=thick)
                if thick > 0:          # outlines are judged filled (WarningWatch fills its hull)
                    cnt, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.fillPoly(m, cnt, 1)
                self.assertEqual(shape_of(m > 0), shape, (shape, thick))

    def test_a_thin_line_is_no_shape(self):
        m = np.zeros((40, 40), bool)
        np.fill_diagonal(m, True)
        self.assertNotIn(shape_of(m), ("square", "circle"))


class ZoneTests(unittest.TestCase):
    ZONES = [(110, "square", (180, 180, 180)), (300, "triangle", (40, 40, 40)), (650, "circle", (40, 200, 100))]

    def test_finds_three_zones_and_their_icons(self):
        z = find_zones(bar(self.ZONES), X0, X1, Y0 + 4, Y1 - 2)
        self.assertEqual([sh for *_, sh in z], ["square", "triangle", "circle"])
        for (a, b, _), (x, *_) in zip(z, self.ZONES):
            self.assertLess(abs((a + b) / 2 - (X0 + x)), 6)

    def test_slider_arrows_are_not_zones(self):
        f = bar([], slider=(380, 540))
        draw_icon(f, X0 + 400, (Y0 + Y1) // 2, "triangle")      # an arrow-ish icon in the slider
        self.assertEqual(find_zones(f, X0, X1, Y0 + 4, Y1 - 2, slider=(X0 + 380, X0 + 540)), [])

    def test_votes_and_elimination_then_steering(self):
        rod = Noiseform()
        for i in range(3):
            rod.see_bar(bar(self.ZONES), 0, reading(), now=i * 0.05)
        self.assertEqual([sh for *_, sh in rod.zones], ["square", "triangle", "circle"])
        rod._zones[2][2].clear()                                      # its icon never read
        self.assertEqual([sh for *_, sh in rod.zones], ["square", "triangle", "circle"])  # by elimination
        # zones up with no circle/triangle warning before: the square, by elimination
        self.assertEqual((rod.want, rod.guessed), ("square", True))
        self.assertLess(abs(rod.aim(0.2) - (X0 + 110)), 6)
        import fischnoise
        fischnoise.STEER = True
        self.addCleanup(setattr, fischnoise, "STEER", False)
        rod.want, rod.want_until = "triangle", 2.0
        self.assertLess(abs(rod.aim(0.2) - (X0 + 300)), 6)
        rod.see_bar(bar([]), 0, reading(), now=1.0)                   # zones gone
        self.assertIsNone(rod.aim(1.0))

    def test_zone_under_the_slider_keeps_its_shape_and_stays(self):
        # Live 18:44: steering onto the circle zone made its icon read "square"
        # under the slider; those votes won and the zone was lost.
        rod = Noiseform()
        for i in range(4):
            rod.see_bar(bar(self.ZONES), 0, reading(), now=i * 0.05)
        over = (600, 700)                                  # slider now over the circle zone
        f = bar([(110, "square", (180, 180, 180)), (300, "triangle", (40, 40, 40)),
                 (650, "square", (40, 200, 100))], slider=over)   # its icon misreads
        for i in range(10):
            rod.see_bar(f, 0, reading(slider=over), now=0.3 + i * 0.05)
        self.assertEqual([sh for *_, sh in rod.zones], ["square", "triangle", "circle"])
        g = bar([(110, "square", (180, 180, 180)), (300, "triangle", (40, 40, 40))], slider=over)
        rod.see_bar(g, 0, reading(slider=over), now=1.5)  # hidden under the slider: kept
        self.assertIn("circle", [sh for *_, sh in rod.zones])
        rod.see_bar(g, 0, reading(slider=over), now=5.0)  # but not forever
        self.assertNotIn("circle", [sh for *_, sh in rod.zones])

    def test_a_zone_needs_three_sightings(self):
        rod = Noiseform()
        rod.see_bar(bar(self.ZONES), 0, reading(), now=0.0)
        rod.see_bar(bar(self.ZONES), 0, reading(), now=0.05)
        self.assertEqual(rod.zones, [])
        self.assertIsNone(rod.want)                        # no round started yet
        rod.see_bar(bar(self.ZONES), 0, reading(), now=0.1)
        self.assertEqual(len(rod.zones), 3)
        self.assertIsNotNone(rod.want)

    def test_recorded_noiseform_bar(self):
        rec = ROOT / "saved_logs" / "20261005_173116" / "rec_01.npz"
        if not rec.exists():
            self.skipTest("recording not present (saved_logs is local only)")
        from replay_skin import load_recording
        frames, _ = load_recording(rec)
        t, f, y, _, r = frames[126]
        z = find_zones(f, r.track_x0, r.track_x1, r.y0 + 4 - y, r.y1 - 2 - y,
                       slider=(r.slider_x0, r.slider_x1))
        self.assertEqual([sh for *_, sh in z], ["square", "triangle", "circle"])


class WarningTests(unittest.TestCase):
    """The warning: a big icon at the screen centre (user's recording, 2026-10-05)."""

    def fixture(self, name):
        p = ROOT / "dev_tests" / "fixtures" / f"{name}.png"
        if not p.exists():
            self.skipTest("fixture not present (git-ignored)")
        return cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)

    def test_recorded_warnings(self):
        self.assertEqual(read_warning(self.fixture("warn_circle")), "circle")
        self.assertEqual(read_warning(self.fixture("warn_triangle")), "triangle")
        self.assertEqual(read_warning(self.fixture("warn_square")), "square")
        self.assertIsNone(read_warning(self.fixture("warn_none")))   # character + particles

    def test_synthetic_warnings(self):
        rng = np.random.default_rng(2)
        box = rng.integers(120, 220, (476, 476, 3)).astype(np.uint8)
        self.assertIsNone(read_warning(box))
        g = box.copy()
        cv2.circle(g, (238, 238), 180, (60, 220, 110), -1)
        self.assertEqual(read_warning(g), "circle")
        t = box.copy()
        cv2.fillPoly(t, [np.array([(238, 90), (70, 380), (406, 380)])], (15, 15, 20))
        self.assertEqual(read_warning(t), "triangle")
        off = box.copy()
        cv2.circle(off, (60, 60), 150, (60, 220, 110), -1)          # big green, not centred
        self.assertIsNone(read_warning(off))

    def test_zones_after_a_warning_or_by_elimination(self):
        rod = Noiseform()
        rod.last_warning = ("triangle", 1.0)
        rod.zones_up(1.4)
        self.assertEqual((rod.want, rod.guessed), ("triangle", False))
        rod = Noiseform()
        rod.zones_up(5.0)                                   # no circle / triangle seen
        self.assertEqual((rod.want, rod.guessed), ("square", True))
        rod = Noiseform()
        rod.last_warning = ("circle", 1.0)
        rod.zones_up(9.0)                                   # an old warning doesn't count
        self.assertEqual(rod.want, "square")


if __name__ == "__main__":
    unittest.main()
