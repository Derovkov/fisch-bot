"""Fast cast: shorter waits between reels, nothing skipped that a totem needs."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischbot
from fischbot import FAST_READ_EVERY, FischBot, MacroConfig
from fischconfig import configuration
from fischui import DEFAULTS


def bot(fast=True, cycles=1):
    b = FischBot.__new__(FischBot)
    b.cfg = MacroConfig(fast_cast=fast)
    b.running, b.cycles, b.quests, b.state = True, cycles, [], "idle"
    b.focus = SimpleNamespace(ready=lambda: True)
    b.mouse = Mock()
    b.log = Mock()
    b._weather_logged = None
    b.weather = Mock()
    b.weather.refresh.return_value = SimpleNamespace(names=("Clear",), complete=True)
    b.useables = None
    return b


class FastCastTests(unittest.TestCase):
    def test_catch_message_wait_is_capped_and_goes_ahead(self):
        b = bot()
        b.catch_watch = SimpleNamespace(error=None, clear_ready=False, poll=lambda *a: None,
                                        ready=lambda now: False)
        clock = [0.0]

        def tick(*_):
            clock[0] += 0.25
            return clock[0]
        orig = fischbot.time
        fischbot.time = SimpleNamespace(perf_counter=lambda: clock[0], sleep=tick)
        try:
            self.assertTrue(b._wait_catch_clear(1.0))           # fast: goes ahead
            self.assertLess(clock[0], 1.6)
            clock[0] = 0.0
            self.assertFalse(b._wait_catch_clear())             # normal: 8s, then retry the cycle
            self.assertGreaterEqual(clock[0], 8)
        finally:
            fischbot.time = orig

    def test_weather_read_every_nth_cast_unless_a_totem_is_due(self):
        b = bot(cycles=1)
        b._use_items()
        b.weather.refresh.assert_not_called()
        b.useables = SimpleNamespace(wants_weather=lambda _: True, active=False)
        b._use_items()
        b.weather.refresh.assert_called_once()                   # a totem is due
        b.useables = None
        b.weather.refresh.reset_mock()
        b.cycles = FAST_READ_EVERY
        b._use_items()
        b.weather.refresh.assert_called_once()
        slow = bot(fast=False, cycles=1)
        slow._use_items()
        slow.weather.refresh.assert_called_once()                # normal: every cast

    def test_quest_tracker_every_nth_cast_after_the_first_read(self):
        b = bot(cycles=1)
        b.track_quests, b._quest_state = True, {}
        b.grabber = Mock()
        b._check_quests()
        b.grabber.grab.assert_not_called()

    def test_setting_is_saved_with_setups_and_defaults_off(self):
        rods = {'Duskwire'}
        self.assertFalse(configuration(dict(DEFAULTS, rod='Duskwire'), DEFAULTS, rods)['fast_cast'])
        self.assertTrue(configuration(dict(DEFAULTS, rod='Duskwire', fast_cast=True), DEFAULTS, rods)['fast_cast'])
        old = {k: v for k, v in DEFAULTS.items() if k != 'fast_cast'}   # a setup saved before this option
        self.assertFalse(configuration(dict(old, rod='Duskwire'), DEFAULTS, rods)['fast_cast'])
        with self.assertRaises(ValueError):
            configuration(dict(DEFAULTS, rod='Duskwire', fast_cast='yes'), DEFAULTS, rods)


if __name__ == '__main__':
    unittest.main()
