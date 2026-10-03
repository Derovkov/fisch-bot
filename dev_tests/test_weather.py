"""Offline weather fixtures, tooltip ambiguity, item guards and cancellation.
No live screen captures or OS input. The real fixture remains git-ignored.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischweather import Icon, WeatherReader, WeatherState
from fischuse import Useables


def fixture_frame():
    path = ROOT / "dev_tests/fixtures/bottom_strip_default_font.png"
    if not path.exists():
        raise unittest.SkipTest("local weather HUD fixture not present")
    im = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    f = np.zeros((1009, 1920, 3), np.uint8)
    f[-im.shape[0]:, :im.shape[1]] = im
    return f


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.r = WeatherReader(clock=lambda: 1000.)

    def test_real_hud_and_resized_client(self):
        f = fixture_frame()
        for scale in (1., .75, .5):
            with self.subTest(scale=scale):
                im = cv2.resize(f, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                icons, complete = self.r.locate(im)
                self.assertTrue(complete)
                self.assertEqual([i.name for i in icons], ["Spring", "Day", "Rain"])
                self.assertAlmostEqual(icons[0].box[0] / scale, 1868, delta=3)

    def test_hidden_hud_and_unknown_icon_are_incomplete(self):
        self.assertEqual(self.r.locate(np.zeros((600, 960, 3), np.uint8)), ([], False))
        f = fixture_frame()
        # An extra, bright square where the next icon would appear is not a
        # recognised weather; it must not be treated as absent.
        f[885:903, 1758:1776] = 255
        icons, complete = self.r.locate(f)
        self.assertFalse(complete)
        self.assertTrue(any(i.name is None for i in icons))

    def test_star_shape_requires_tooltip_not_a_grayscale_guess(self):
        f = fixture_frame()
        # The wiki has three identical star silhouettes with different colours.
        im = self.r.images["Shiny Surge"]
        rgba = cv2.resize(im, (38, 38), interpolation=cv2.INTER_AREA)
        alpha = rgba[:, :, 3:4].astype(float) / 255
        f[875:913, 1748:1786] = rgba[:, :, :3][:, :, ::-1] * alpha
        icons, complete = self.r.locate(f)
        self.assertFalse(complete)
        self.assertIn("Shiny Surge", icons[-1].candidates)
        self.assertIsNone(icons[-1].name)
        self.assertEqual(self.r.tooltip_name([("Shiny Surge", (0, 0, 80, 20))],
                                           icons[-1].candidates), "Shiny Surge")

    def test_tooltip_alias_clock_and_unrelated_description(self):
        read = lambda s: [(s, (0, 0, 100, 20))]
        self.assertEqual(self.r.tooltip_name(read("Night 02:34"), ("Night",)), "Night")
        self.assertEqual(self.r.tooltip_name(read("Night of the Luminous")), "Day/Night of the Luminous")
        self.assertIsNone(self.r.tooltip_name(read("Rain"), ("Shiny Surge",)))
        self.assertIsNone(self.r.tooltip_name(read("Weather changes at night")))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.r = WeatherReader(clock=lambda: 1000.)
        self.u = Useables({"enabled": True, "totems": [{"name": "Aurora Totem", "when": "start"}]},
                          lambda _: None, clock=lambda: 1000.)
        self.totems = self.u._totem_info

    def state(self, *conditions):
        self.r.state = WeatherState(("Spring", "Night", *conditions), True, 1000.)

    def test_active_effect_and_same_group_protection(self):
        self.state("Rain", "Aurora Borealis")
        self.assertIn("already active", self.r.block_reason(self.totems["Aurora Totem"]))
        self.assertIn("protects", self.r.block_reason(self.totems["Smokescreen Totem"]))
        # Modifiers and time belong to separate groups, not the weather group.
        self.assertIsNone(self.r.block_reason(self.totems["Shiny Totem"]))
        self.assertIsNone(self.r.block_reason(self.totems["Sundial Totem"]))
        self.state("Rain", "Shiny Surge")
        self.assertIn("already active", self.r.block_reason(self.totems["Shiny Totem"]))
        self.assertIsNone(self.r.block_reason(self.totems["Aurora Totem"]))

    def test_missing_stale_unknown_and_local_rules_defer(self):
        self.assertIn("waiting", self.r.block_reason(self.totems["Aurora Totem"]))
        self.state("Rain")
        self.r.state.checked_at = 994.
        self.assertIn("waiting", self.r.block_reason(self.totems["Aurora Totem"]))
        self.state("Rain", "new event")
        self.assertIn("waiting", self.r.block_reason(self.totems["Aurora Totem"]))
        self.state("Rain")
        self.assertIn("location", self.r.block_reason(self.totems["Blizzard Totem"]))

    def test_defer_does_not_spend_start_trigger_or_item_limit(self):
        bot = SimpleNamespace(weather=self.r, running=True, quests=[])
        self.state("Aurora Borealis")
        with patch.object(self.u, "use_totem", return_value=False) as use, \
                patch.object(self.r, "refresh", side_effect=lambda *a, **k: self.r.state):
            self.u.between_casts(bot)
            use.assert_not_called()
            self.assertEqual(self.u.st("Aurora Totem").uses, 0)
            self.assertIsNone(self.u.st("Aurora Totem").last_used)
            self.state("Rain")
            self.u.between_casts(bot)
            use.assert_called_once()

    def test_recheck_after_first_totem_blocks_second(self):
        self.u.update({"enabled": True, "totems": [{"name": "Aurora Totem"}, {"name": "Starfall Totem"}]})
        bot = SimpleNamespace(weather=self.r, running=True, quests=[])
        self.state("Rain")
        def refresh(*args, **kwargs):
            if use.call_count:
                self.state("Aurora Borealis")
            return self.r.state
        with patch.object(self.u, "use_totem", return_value=True) as use, \
                patch.object(self.r, "refresh", side_effect=refresh):
            self.u.between_casts(bot)
            self.assertEqual(use.call_count, 1)
            self.assertIn("protects", self.u.st("Starfall Totem").status)

    def test_due_but_active_totem_does_not_force_hovers_each_cast(self):
        from fischbot import FischBot
        self.u.update({"enabled": True, "totems": [{"name": "Mutation Totem"}]})
        self.state("Windy", "Mutation Surge")
        bot = FischBot.__new__(FischBot)
        bot.running, bot.quests, bot.state = True, [], "idle"
        bot.focus = SimpleNamespace(ready=lambda: True)
        bot.mouse = SimpleNamespace(release=lambda: None)
        bot.useables, bot.weather, bot._weather_logged = self.u, self.r, None
        bot.log = lambda _: None
        with patch.object(self.r, "refresh", return_value=self.r.state) as refresh, \
                patch.object(self.u, "use_totem") as use:
            for _ in range(3):
                bot._use_items()
            self.assertEqual(refresh.call_count, 3)
            self.assertTrue(all(c.kwargs.get("confirm", False) is False for c in refresh.call_args_list))
            use.assert_not_called()
        self.assertIn("already active", self.u.st("Mutation Totem").status)

    def test_effect_appearing_during_preuse_confirmation_prevents_use(self):
        self.state("Rain")
        bot = SimpleNamespace(weather=self.r, running=True, quests=[])
        with patch.object(self.r, "refresh", side_effect=lambda *a, **k: self.state("Aurora Borealis")) as read, \
                patch.object(self.u, "use_totem") as use:
            self.u.between_casts(bot)
            read.assert_called_once_with(bot, confirm=True)
            use.assert_not_called()


class HoverTests(unittest.TestCase):
    def setUp(self):
        self.r = WeatherReader(clock=lambda: 1000.)
        self.bot = SimpleNamespace(running=True, focus=SimpleNamespace(ready=lambda: True),
            mouse=SimpleNamespace(dry_run=False, release=lambda: None),
            rect=SimpleNamespace(left=10, top=20), recentre=lambda: None)
        self.base = np.zeros((600, 960, 3), np.uint8)
        self.after = np.full_like(self.base, 150)
        self.bot.grabber = SimpleNamespace(grab=lambda: self.base)
        self.icons = [Icon((x, 400, 38, 38), (name,), .9, name, (name,), "icon")
                      for x, name in [(900, "Spring"), (860, "Night"), (820, "Rain")]]

    def test_hover_offsets_confirms_and_unchanged_row_is_cached(self):
        captures = iter([self.base, self.after, self.base, self.after, self.base, self.after, self.base,
                         self.base])
        self.bot.grabber.grab = lambda: next(captures)
        reads = [[(n, (20, 20, 100, 20))] for n in ("Spring", "Night", "Rain")]
        moved = []
        with patch.object(self.r, "locate", return_value=(self.icons, True)), \
                patch("fischequip.WinInput", return_value=SimpleNamespace(move=lambda *xy: moved.append(xy))), \
                patch("fischocr.ocr_lines", side_effect=reads) as ocr, patch("fischweather.HOVER_S", 0):
            self.assertTrue(self.r.refresh(self.bot).complete)
            self.assertTrue(self.r.refresh(self.bot).complete)
        self.assertEqual(moved, [(929, 439), (889, 439), (849, 439)])
        self.assertEqual(ocr.call_count, 3)
        self.assertTrue(all(i.source == "tooltip" for i in self.r.state.icons))

    def test_stop_or_focus_loss_mid_hover_sends_no_further_input(self):
        for stop in (True, False):
            with self.subTest(stop=stop):
                self.bot.running = True
                self.bot.focus.ready = lambda: True
                moves, centres = [], []
                self.bot.recentre = lambda: centres.append(1)
                def move(*xy):
                    moves.append(xy)
                    if stop:
                        self.bot.running = False
                    else:
                        self.bot.focus.ready = lambda: False
                with patch.object(self.r, "locate", return_value=(self.icons, True)), \
                        patch("fischequip.WinInput", return_value=SimpleNamespace(move=move)), \
                        patch("fischocr.ocr_lines") as ocr:
                    self.assertFalse(self.r.refresh(self.bot, confirm=True).complete)
                    ocr.assert_not_called()
                self.assertEqual(len(moves), 1)
                self.assertEqual(centres, [])

    def test_unique_shapes_do_not_rehover_on_cache_expiry(self):
        self.r.clock = lambda: 1061.
        self.r._tooltips = {i.fingerprint: (1000., i.name, "tooltip") for i in self.icons}
        with patch.object(self.r, "locate", return_value=(self.icons, True)), \
                patch("fischequip.WinInput") as inp, patch("fischocr.ocr_lines") as ocr:
            self.assertTrue(self.r.refresh(self.bot).complete)
            inp.assert_not_called()
            ocr.assert_not_called()

    def test_briefly_hidden_hud_keeps_cache_without_claiming_clear_weather(self):
        self.r._tooltips = {i.fingerprint: (1000., i.name, "tooltip") for i in self.icons}
        with patch.object(self.r, "locate", side_effect=[([], False), (self.icons, True)]), \
                patch("fischequip.WinInput") as inp, patch("fischocr.ocr_lines") as ocr:
            self.assertFalse(self.r.refresh(self.bot).complete)
            self.assertEqual(self.r.state.names, ())
            self.assertTrue(self.r.refresh(self.bot).complete)
            inp.assert_not_called()
            ocr.assert_not_called()

    def test_expired_ambiguous_modifier_still_rechecks_tooltip(self):
        self.r.clock = lambda: 1061.
        star = Icon((780, 400, 38, 38), ("Mutation Surge", "Shiny Surge"), .9,
                    fingerprint=((600, 960), ("Mutation Surge", "Shiny Surge"), (240, 140, 150)))
        row = self.icons + [star]
        self.r._tooltips = {i.fingerprint: (1000., i.name or "Mutation Surge", "tooltip") for i in row}
        captures = iter([self.base, self.after, self.base])
        self.bot.grabber.grab = lambda: next(captures)
        with patch.object(self.r, "locate", return_value=(row, False)), \
                patch("fischequip.WinInput", return_value=SimpleNamespace(move=lambda *xy: None)) as inp, \
                patch("fischocr.ocr_lines", return_value=[("Mutation Surge", (20, 20, 100, 20))]) as ocr, \
                patch("fischweather.HOVER_S", 0):
            self.assertTrue(self.r.refresh(self.bot).complete)
            self.assertEqual(inp.call_count, 1)
            self.assertEqual(ocr.call_count, 1)

    def test_colour_jitter_reuses_cache_but_new_modifier_colour_does_not(self):
        candidates = ("Mutation Surge", "Shiny Surge")
        key = ((600, 960), candidates, (220, 150, 160))
        cached = (1000., "Mutation Surge", "tooltip")
        self.r._tooltips[key] = cached
        icon = Icon((820, 400, 40, 40), candidates, .9,
                    fingerprint=((600, 960), candidates, (224, 153, 156)))
        self.assertEqual(self.r._cached(icon), cached)
        icon.fingerprint = ((600, 960), candidates, (120, 200, 240))
        self.assertIsNone(self.r._cached(icon))

    def test_capture_error_invalidates_old_state_and_restores_cursor(self):
        self.r.state = WeatherState(("Spring", "Night", "Rain"), True, 1000.)
        captures = iter([self.base, RuntimeError("capture failed")])
        def grab():
            item = next(captures)
            if isinstance(item, Exception):
                raise item
            return item
        self.bot.grabber.grab = grab
        centres = []
        self.bot.recentre = lambda: centres.append(1)
        with patch.object(self.r, "locate", return_value=(self.icons, True)), \
                patch("fischequip.WinInput", return_value=SimpleNamespace(move=lambda *xy: None)), \
                patch("fischweather.HOVER_S", 0):
            with self.assertRaises(RuntimeError):
                self.r.refresh(self.bot, confirm=True)
        self.assertFalse(self.r.state.complete)
        self.assertEqual(centres, [1])


if __name__ == "__main__":
    unittest.main()
