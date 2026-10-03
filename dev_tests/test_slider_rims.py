"""Regressions from real saved crops, including window scale and capture offsets.

Run: python -m unittest discover -s dev_tests -p test_slider_rims.py
Only reads saved files. Never captures the screen or sends input.
"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischtrack as ft


def crop(run, name):
    path = ROOT / 'saved_logs' / ('20261003_' + run) / name
    if not path.exists():
        raise unittest.SkipTest(f'Local crop fixture missing: {path.name}')
    return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)


class SliderRimTests(unittest.TestCase):
    def setUp(self):
        ft.set_client(1920, 1009)
        ft.lock_scale(1)

    def start(self, run, first, width):
        f = crop(run, first)
        ft.set_geometry(track_x_frac=(60 / f.shape[1], 837 / f.shape[1]))
        r = ft.TrackReading(60, 837, 50, 80, 448 - width // 2,
                            448 + width // 2 - 1, 448)
        tr = ft.SkinTracker(f, r, now=0)
        for i in range(6):
            tr.read(f, now=.03 * (i + 1))
        return tr

    def test_slider_recovers_from_old_wrong_position(self):
        # Old live trace: slider 837-1005 in client coordinates. The saved
        # image actually shows crop x70-327, with fish x192 inside it.
        tr = self.start('094007', 'bar_0003.27_hit.png', 258)
        f = crop('094007', 'bar_0009.34_hit.png')
        q = tr.read(f, now=6.07)
        self.assertIsNotNone(q, tr.why)
        self.assertLessEqual(abs(q.slider_x0 - 70), 6)
        self.assertLessEqual(abs(q.slider_x1 - 327), 6)
        self.assertLessEqual(abs(q.marker_x - 192), 3)
        self.assertTrue(q.fish_inside)
        self.assertGreaterEqual(tr.w, 250)

    def test_claws_do_not_shrink_width_to_an_icon(self):
        tr = self.start('093715', 'bar_0003.26_hit.png', 174)
        q = tr.read(crop('093715', 'bar_0019.26_hit.png'), now=16)
        self.assertIsNotNone(q, tr.why)
        self.assertLessEqual(abs(q.slider_x0 - 175), 6)
        self.assertLessEqual(abs(q.slider_x1 - 348), 6)
        self.assertGreaterEqual(tr.w, 168)

    def test_white_star_is_not_a_grey_fish(self):
        tr = self.start('093715', 'bar_0003.26_hit.png', 174)
        q = tr.read(crop('093715', 'bar_0006.73_hit.png'), now=3.47)
        # The true capsule is partially covered. Rejecting it is acceptable;
        # returning the white star at crop x710 as the fish is not.
        if q is not None:
            self.assertLessEqual(abs(q.marker_x - 641), 4)
        self.assertGreaterEqual(tr.w, 168)

    def test_half_scale_duskwire_is_not_a_half_slider(self):
        f = crop('083155', 'bar_0003.00_miss.png')
        ft.lock_scale(.5)
        ft.set_geometry(track_x_frac=(30 / f.shape[1], 418 / f.shape[1]))
        r = ft.TrackReading(30, 418, 25, 40, 195, 253, 224, scale=.5)
        tr = ft.SkinTracker(f, r)
        q = tr.read(f, now=.03)
        self.assertIsNotNone(q, tr.why)
        self.assertLessEqual(abs(q.slider_x0 - 195), 3)
        self.assertLessEqual(abs(q.slider_x1 - 253), 3)
        self.assertLessEqual(abs(q.marker_x - 224), 2)

    def test_capture_band_keeps_client_coordinates(self):
        tr_full = self.start('094007', 'bar_0003.27_hit.png', 258)
        tr_band = self.start('094007', 'bar_0003.27_hit.png', 258)
        f = crop('094007', 'bar_0009.34_hit.png')
        full = tr_full.read(f, now=6.07)
        band = tr_band.read(np.ascontiguousarray(f[15:125]), y_off=15, now=6.07)
        self.assertIsNotNone(full, tr_full.why)
        self.assertIsNotNone(band, tr_band.why)
        self.assertEqual(vars(full), vars(band))

    def test_thin_effect_leaves_two_real_marker_edges(self):
        tr = self.start('104218', 'bar_0005.22_hit.png', 174)
        q = tr.read(crop('104218', 'bar_0007.10_miss.png'), now=1.88)
        self.assertIsNotNone(q, tr.why)
        self.assertLessEqual(abs(q.marker_x - 448), 3)
        self.assertTrue(q.fish_inside)


if __name__ == '__main__':
    unittest.main()
