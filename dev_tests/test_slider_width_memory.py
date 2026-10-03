"""The run's learned slider width steers the skin tracker's start.

Live 2026-10-03 (saved_logs/20261003_172758, Fabulous Rod): green claw
decorations either side of the slider read as its edges -- 76px instead of
116px -- and the reel was followed at 66% inside."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischtrack import SkinTracker, TrackReading  # noqa: E402

FIX = ROOT / "dev_tests" / "fixtures"


def _frame(name):
    import cv2
    im = cv2.imread(str(FIX / name))[:, :, ::-1]
    f = np.zeros((1009, 1920, 3), np.uint8)
    f[796:796 + im.shape[0], 511:511 + im.shape[1]] = im     # where the crop was cut
    return f


START = TrackReading(572, 1347, 846, 876, 902, 1017, 960, scale=1.0, method="geo")


class SliderWidthMemoryTests(unittest.TestCase):
    def test_claws_do_not_shrink_the_slider_once_its_width_is_known(self):
        if not (FIX / "bar_fabulous_claws.png").exists():
            self.skipTest("local bar crops not present")
        f = _frame("bar_fabulous_claws.png")
        self.assertLess(SkinTracker(f, START).w, 80)            # the live failure
        t = SkinTracker(f, START, expect_w=116)
        self.assertTrue(abs(t.w - 116) <= 14, t.w)
        self.assertGreaterEqual(t.base_w, 102)                  # can't narrow to the claws later

    def test_clean_reel_start_unchanged(self):
        if not (FIX / "bar_fabulous_plain.png").exists():
            self.skipTest("local bar crops not present")
        f = _frame("bar_fabulous_plain.png")
        self.assertEqual(SkinTracker(f, START).w, SkinTracker(f, START, expect_w=116).w)

    def test_off_width_refind_keeps_its_centre(self):
        f = np.full((1009, 1920, 3), 30, np.uint8)
        r = TrackReading(572, 1347, 846, 876, 700, 760, 730, scale=1.0, method="geo")
        t = SkinTracker(f, r, centred=False, expect_w=116)
        a = t.reading()
        self.assertEqual(t.w, 116)
        self.assertTrue(abs((a.slider_x0 + a.slider_x1) / 2 - 730) <= 2)


if __name__ == "__main__":
    unittest.main()
