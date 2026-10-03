"""Catch text, freshness, nonblocking OCR, and outcome precedence. No game input."""
from pathlib import Path
import sys
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fischcatch import CatchNotice, CatchWatch, catch_notice
from fischbot import FischBot, MacroConfig, ProgressTrend
from fischcontrol import ReelController
from fischtrack import TrackReading


# Actual Windows OCR output from the user's Duskwire screenshot.
PASSIVE = [
    ('Chaotic', (45, 15, 141, 39)),
    ('energy surges throush your strinc,s...', (152, 15, 631, 49)),
    ('Enchant Relic at 211<9! (14.59%)', (319, 43, 660, 71)),
    ('You just caught', (15, 44, 195, 71)),
]
NORMAL = [('You just caught a Big Sockeye Salmon', (10, 20, 500, 45))]


class PhraseTests(unittest.TestCase):
    def test_user_image_split_colours_and_ocr_errors(self):
        self.assertEqual(catch_notice(PASSIVE, 'Duskwire').source, 'duskwire passive')

    def test_passive_alone_confirms_duskwire(self):
        self.assertEqual(catch_notice(PASSIVE[:2], 'Duskwire').source, 'duskwire passive')
        self.assertIsNone(catch_notice(PASSIVE[:2], 'Crew Rod'))

    def test_other_rods_and_normal_message_fallback(self):
        for rod in ['Duskwire', 'Crew Rod', 'Fabulous Rod']:
            self.assertEqual(catch_notice(NORMAL, rod).source, 'caught message')
        self.assertEqual(catch_notice(PASSIVE[2:], 'Duskwire').source, 'caught message')

    def test_unrelated_ui_is_not_a_catch(self):
        for text in ['Progress Speed: 100%', 'Perfect!', 'Chaotic',
                     'Current Bait: Instant Catcher', 'caught',
                     'Chaotic energy charges your reel']:
            self.assertIsNone(catch_notice([(text, (0, 0, 400, 25))], 'Duskwire'))

    def test_different_rows_cannot_build_a_fake_message(self):
        self.assertIsNone(catch_notice([
            ('You just', (0, 0, 100, 20)), ('caught', (0, 60, 100, 80))], 'Duskwire'))


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        # Inject completed worker responses; tests don't invoke Windows OCR.
        self.watch = CatchWatch('Duskwire', reader=lambda image: [])

    def tearDown(self):
        self.watch.close()

    def result(self, notice, sampled_at, active_since=None, epoch=None):
        self.watch._results.put_nowait((self.watch._epoch if epoch is None else epoch,
                                       sampled_at, notice, None))
        return self.watch.poll(sampled_at + .01, active_since)

    def clear(self, t=0):
        self.result(None, t)
        self.result(None, t + .25)

    def test_lingering_notice_is_not_next_fish(self):
        notice = CatchNotice('duskwire passive')
        self.result(notice, 0)
        self.watch.begin_attempt()
        self.assertIsNone(self.result(notice, 2, active_since=1))
        self.assertFalse(self.watch.clear_ready)
        self.clear(3)
        self.assertEqual(self.result(notice, 4, active_since=1), notice)

    def test_two_identical_passives_confirm_separate_catches_after_clear(self):
        notice = CatchNotice('duskwire passive')
        for attempt in range(2):
            self.clear(attempt * 10)
            self.watch.begin_attempt()
            self.assertEqual(self.result(notice, attempt * 10 + 2,
                                         active_since=attempt * 10 + 1), notice)
            self.watch.end_attempt()
            self.assertIsNone(self.result(notice, attempt * 10 + 3,
                                          active_since=attempt * 10 + 2))

    def test_older_frame_and_old_attempt_cannot_confirm(self):
        self.clear()
        old_epoch = self.watch._epoch
        self.watch.begin_attempt()
        notice = CatchNotice('caught message')
        self.assertIsNone(self.result(notice, .4, active_since=.5, epoch=old_epoch))
        self.assertIsNone(self.result(notice, .4, active_since=.5))

    def test_slow_ocr_never_blocks_sampling_poll_or_stop(self):
        entered, unblock = Event(), Event()

        def slow_reader(image):
            entered.set()
            unblock.wait(3)
            return NORMAL

        self.watch.close()
        self.watch = CatchWatch('Duskwire', reader=slow_reader)
        try:
            self.watch.sample(np.zeros((5, 5, 3), np.uint8), 0)
            self.assertTrue(entered.wait(.5))
            self.assertFalse(self.watch.ready(.5))
            self.assertIsNone(self.watch.poll(2, active_since=0))
            self.assertEqual(self.watch.error, 'OCR timeout')
            t0 = time.perf_counter()
            self.watch.close()
            self.assertLess(time.perf_counter() - t0, .1)
        finally:
            unblock.set()

    def test_worker_exception_disables_optional_reader(self):
        self.watch._results.put_nowait((self.watch._epoch, 0, None, 'RuntimeError'))
        self.assertIsNone(self.watch.poll(.1, 0))
        self.assertEqual(self.watch.error, 'RuntimeError')
        self.assertFalse(self.watch.ready(1))


class OutcomeTests(unittest.TestCase):
    def bot(self):
        b = FischBot.__new__(FischBot)
        b.ctl = ReelController()
        b.ctl.begin(0)
        b.ctl.stats.frames = 10
        b.mouse = SimpleNamespace(release=lambda: None)
        b.catch_watch = CatchWatch('Duskwire', reader=lambda image: [])
        self.addCleanup(b.catch_watch.close)
        b.reel_logs, b.reel_history = [], []
        b.log = lambda text: None
        b.last_prog_top = 950
        return b

    def test_passive_ends_catch_despite_false_progress_and_clears_old_geometry(self):
        b = self.bot()
        trend = ProgressTrend()
        trend.add(1, .27)
        self.assertTrue(b._finish_reel(0, 1, trend, CatchNotice('duskwire passive')))
        self.assertTrue(b.reel_history[0]['caught'])
        self.assertEqual(b.reel_history[0]['confirmation'], 'duskwire passive')
        self.assertIn('CAUGHT (duskwire passive)', b.reel_logs[0])
        self.assertNotIn('ESCAPED', b.reel_logs[0])
        self.assertIsNone(b.last_prog_top)

    def test_normal_message_and_progress_fallback(self):
        for notice, peak, expected in [
            (CatchNotice('caught message'), .27, True),
            (None, .99, True), (None, .27, False),
        ]:
            b = self.bot()
            trend = ProgressTrend()
            trend.add(1, peak)
            self.assertEqual(b._finish_reel(0, 1, trend, notice), expected)

    def test_live_reel_loop_exits_on_notice_even_while_bar_remains_visible(self):
        # Geometry never disappears and progress never exceeds 27%. The catch
        # notice must stop input and end this attempt before the reel timeout.
        mouse = SimpleNamespace(down=True)
        mouse.release = lambda: setattr(mouse, 'down', False)
        mouse.set_down = lambda value: setattr(mouse, 'down', value)
        b = FischBot(MacroConfig(reel_timeout_s=1), None, None,
                     SimpleNamespace(width=1920, height=1009), None, mouse,
                     SimpleNamespace(ready=lambda: True),
                     session=SimpleNamespace(), on_log=lambda text: None)
        b.catch_watch.close()
        b.catch_watch = SimpleNamespace(
            error=None, ready=lambda now: False, end_attempt=lambda: None,
            poll=lambda now, active_since: CatchNotice('duskwire passive')
            if active_since is not None else None)
        b.running = True
        r = TrackReading(571, 1348, 865, 895, 834, 1085, 960)
        image = np.zeros((110, 1920, 3), np.uint8)
        b._grab_and_read = lambda hint, tracker: (r, time.perf_counter(), image, 845)
        b.learner.feed = lambda *args: None
        with patch('fischbot.find_progress', return_value=(.27, 89)):
            self.assertTrue(b.reel())
        self.assertFalse(mouse.down)
        self.assertEqual(len(b.reel_history), 1)
        self.assertEqual(b.reel_history[0]['confirmation'], 'duskwire passive')


if __name__ == '__main__':
    unittest.main()
