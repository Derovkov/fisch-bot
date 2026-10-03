"""Real progress-bar regressions and controller timing checks; no game input."""
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischtrack as ft
from fischbot import CAUGHT_PEAK, ProgressTrend
from fischcontrol import ControlConfig, ReelController


def saved_crop(run, name):
    path = ROOT / 'saved_logs' / ('20261003_' + run) / name
    if not path.exists():
        raise unittest.SkipTest(f'Local fixture missing: {path.name}')
    crop = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    # Restore only the crop's horizontal client coordinates for _prog_x.
    frame = np.zeros((crop.shape[0], 1920, 3), np.uint8)
    frame[:, 511:511 + crop.shape[1]] = crop
    return frame


class ProgressTests(unittest.TestCase):
    def setUp(self):
        ft.set_client(1920, 1009)
        ft.lock_scale(1)

    def test_real_duskwire_dark_fill_increases(self):
        state = ft.ProgressPolarity()
        first = saved_crop('104039', 'bar_0004.68_hit.png')
        later = saved_crop('104039', 'bar_0007.76_hit.png')
        raw = ft.find_progress(first, 110, 140)
        start = ft.find_progress(first, 110, 140, polarity=state)
        end = ft.find_progress(later, 110, 140, polarity=state)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertFalse(state.bright_fill)
        self.assertGreater(raw[0], .9)
        self.assertLess(start[0], .1)
        self.assertGreater(end[0], start[0] + .3)
        # Entirely dark at completion, entirely pale at zero progress.
        self.assertEqual(state.fill(np.zeros((4, 414), bool)), 1)
        self.assertEqual(state.fill(np.ones((4, 414), bool)), 0)

    def test_crew_and_per_reel_reset(self):
        state = ft.ProgressPolarity()
        f = saved_crop('104218', 'bar_0005.22_hit.png')
        p = ft.find_progress(f, 110, 140, polarity=state)
        self.assertIsNotNone(p)
        self.assertTrue(state.bright_fill)
        self.assertLess(p[0], .1)
        self.assertEqual(state.fill(np.ones((4, 414), bool)), 1)
        fresh = ft.ProgressPolarity()
        self.assertIsNone(fresh.fill(np.ones((4, 414), bool)))
        self.assertIsNone(fresh.bright_fill)

    def test_all_six_user_confirmed_duskwire_catches(self):
        paths = list((ROOT / 'saved_logs/20261003_104039').glob('trace_*.txt'))
        if not paths:
            self.skipTest('Local Duskwire trace missing')
        episodes = []
        trend = None
        for line in paths[0].read_text().splitlines():
            m = re.search(r't=\s*([\d.]+) (wait|reel)', line)
            if not m:
                continue
            if m[2] == 'wait':
                if trend is not None:
                    episodes.append(trend)
                trend = None
                continue
            if trend is None:
                trend = ProgressTrend()
            p = re.search(r'prog=([\d.]+)@', line)
            if p:
                # Polarity is verified against this run's real crops above.
                trend.add(float(m[1]), 1.0 - float(p[1]))
        if trend is not None:
            episodes.append(trend)
        self.assertEqual(len(episodes), 6)
        self.assertTrue(all(t.peak >= CAUGHT_PEAK for t in episodes))


class MotionTests(unittest.TestCase):
    def test_velocity_filter_uses_elapsed_time(self):
        velocities = []
        for dt in (.03, .06):
            c = ReelController()
            c.begin(0)
            for t in np.arange(0, .1201, dt):
                c._update_velocity(float(t), float(t * 100), float(t * 50))
            velocities.append((c.v_slider, c.v_fish))
        np.testing.assert_allclose(velocities[0], velocities[1], atol=1e-9)

    def test_braking_responds_to_acceleration(self):
        c = ReelController()
        c.begin(0)
        actions = []
        for t, x in [(0, 0), (.03, 1), (.06, 4), (.09, 9)]:
            r = SimpleNamespace(slider_centre=x, fish_x=50, error=50-x,
                                slider_w=116, fish_inside=True)
            actions.append(c.step(r, now=t))
        self.assertFalse(actions[-1])  # brake while the slider is still left of fish

    def test_reacquisition_forgets_velocity_preserves_input(self):
        c = ReelController()
        c.begin(0)
        c._update_velocity(0, 800, 850)
        c._update_velocity(.06, 820, 860)
        c.held = True
        c.stats.frames = 20
        c.reset_motion()
        self.assertTrue(c.held)
        self.assertEqual(c.stats.frames, 20)
        c._update_velocity(.08, 1000, 900)
        self.assertEqual((c.v_slider, c.v_fish), (0, 0))


if __name__ == '__main__':
    unittest.main()
