"""Lullaby: its wide pastel slider, and presses timed to the metronome."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischtrack as ft  # noqa: E402
from fischlullaby import Lullaby, Metronome  # noqa: E402
from fischrods import rod_control, slider_frac_for  # noqa: E402

FIX = ROOT / "dev_tests" / "fixtures"
TRACK = (43, 1034, 370)          # x0, x1, top in the wiki GIF (Resistant Composition)


def fixture(name):
    p = FIX / name
    if not p.exists():
        raise unittest.SkipTest(f"{name} not present (git-ignored)")
    return cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)


class MetronomeTests(unittest.TestCase):
    def test_sections_and_wand_on_the_wiki_gif(self):
        m = Metronome()
        m.update(fixture("lullaby_metronome_66.png"), *TRACK)     # wand straight up
        self.assertTrue(any(a <= 90 <= b for a, b in m.sections))  # the top section
        self.assertTrue(m.over_section())
        self.assertLess(abs(m.wand - 100), 8)
        m.update(fixture("lullaby_metronome_30.png"), *TRACK)     # wand out to the right
        self.assertFalse(m.over_section())
        self.assertLess(abs(m.wand - 21), 8)


class FakeMetro:
    def __init__(self):
        self.over = False
        self.sections = []

    def update(self, *a):
        pass

    def over_section(self):
        return self.over


class GateTests(unittest.TestCase):
    def rod(self):
        r = Lullaby()
        r.metro = FakeMetro()
        return r

    def test_a_press_waits_for_a_section_but_not_too_long(self):
        r = self.rod()
        self.assertEqual(r.gate(None, 0, 1, 0, 0.0, True, False), (None, False))   # off-beat: wait
        self.assertEqual(r.gate(None, 0, 1, 0, 0.2, True, False), (None, False))
        r.metro.over = True
        self.assertEqual(r.gate(None, 0, 1, 0, 0.25, True, False), (True, False))  # on the beat
        r.metro.over = False
        r.gate(None, 0, 1, 0, 1.0, True, False)
        self.assertEqual(r.gate(None, 0, 1, 0, 1.5, True, False), (True, False))   # the fish first
        self.assertEqual((r.on_beat, r.forced), (1, 1))

    def test_releases_are_never_held_back_and_one_tap_per_section(self):
        r = self.rod()
        self.assertEqual(r.gate(None, 0, 1, 0, 0.0, False, True), (False, False))
        r.metro.over = True
        self.assertEqual(r.gate(None, 0, 1, 0, 0.1, None, False), (None, True))    # entered: tap
        self.assertEqual(r.gate(None, 0, 1, 0, 0.2, None, False), (None, False))   # same section
        r.metro.over = None                                                        # wand unseen
        self.assertEqual(r.gate(None, 0, 1, 0, 0.3, True, False), (True, False))


class WideSliderTests(unittest.TestCase):
    def test_lullaby_control_sets_how_wide_a_slider_may_be(self):
        self.assertAlmostEqual(rod_control("Lullaby", ["Herculean"]), 0.45, places=2)
        im = fixture("bar_lullaby_herculean.png")
        fr = np.full((1009, 1920, 3), 120, np.uint8)
        fr[800:800 + im.shape[0], 511:511 + im.shape[1]] = im
        ft.set_client(1920, 1009)
        ft.set_scene_clear(True)
        self.addCleanup(ft.set_scene_clear, False)
        self.addCleanup(ft.set_slider_max_frac, None)
        ft.set_slider_max_frac(None)
        r = ft.read_track(fr)                                      # default: too wide --
        self.assertTrue(r is None or r.slider_x0 != 571)           # not the pastel slider
        ft.set_slider_max_frac(slider_frac_for("Lullaby", ["Herculean"]) + 0.12)
        r = ft.read_track(fr)
        self.assertIsNotNone(r)
        self.assertEqual(r.slider_x0, 571)
        self.assertGreater(r.slider_x1, 1180)
        self.assertLess(abs(r.marker_x - 1089), 6)


if __name__ == "__main__":
    unittest.main()
