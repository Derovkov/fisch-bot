"""Rod recovery and responsive equipment geometry, with all input mocked."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischequip as fe
from fischuse import Hotbar
from fischbot import FischBot
from fastcap import WinRect
from fischscan import parse_rod_screen


class GeometryTests(unittest.TestCase):
    def test_small_font_button_slips_and_unrelated_labels(self):
        for text in ("[Equip]", "[Equlp]", "Eguip"):
            self.assertEqual(fe.button_kind(text), "equip")
        for text in ("Equipped", "Equlpped"):
            self.assertEqual(fe.button_kind(text), "equipped")
        for text in ("Equipment Bag", "Equip rod", "Equip bait", "Equine"):
            self.assertIsNone(fe.button_kind(text))

    def test_card_footer_ocr_uses_client_coordinates_and_no_guess(self):
        frame = np.zeros((1000, 852, 3), np.uint8)
        card = {"rod": "Duskwire", "box": (100, 0, 350, 750), "bottom": 900}
        menu = fe.EquipmentMenu(lambda: frame, SimpleNamespace(left=200, top=60),
                                lambda _: None, inp=Mock())
        with patch("fischequip.ocr_lines", return_value=[("[Equlp]", (40, 40, 80, 55))]) as ocr:
            button = menu._button(fe.Screen(), card)
            self.assertEqual(button, ("equip", (140, 787, 180, 802)))
            self.assertEqual(ocr.call_args.args[0].shape, (153, 250, 3))
            menu.click(button[1])
            menu.inp.click.assert_called_once_with(360, 854)
        with patch("fischequip.ocr_lines", return_value=[]):
            self.assertIsNone(menu._button(fe.Screen(), card))

    def test_grid_names_do_not_create_zero_width_or_borrow_next_row_button(self):
        lines = [("[Duskwire]", (300, 340, 410, 360)), ("[Equip]", (325, 380, 385, 395)),
                 ("[Fabulous Rod]", (295, 640, 415, 660)), ("Equipped", (320, 680, 390, 695))]
        cards = parse_rod_screen(np.zeros((1000, 852, 3), np.uint8), lines)
        self.assertEqual(len(cards), 2)
        first, second = cards
        self.assertGreater(first["box"][2] - first["box"][0], 100)
        self.assertFalse(first["equipped"])
        self.assertTrue(second["equipped"])
        self.assertEqual(fe.Screen(lines).button(first)[0], "equip")
        self.assertIsNone(fe.Screen(lines[-1:]).button(first))

    def test_narrow_tall_window_can_have_unscaled_hotbar_slots(self):
        cs = 852 / 2 + 69 * (np.arange(9) - 4)
        words = [("Item", (x - 15, 20, x + 15, 30)) for x in cs]
        centres, pitch = fe.fit_hotbar(words, 852)
        self.assertEqual(len(centres), 9)
        self.assertAlmostEqual(pitch, 69, delta=2)
        self.assertLess(np.max(np.abs(centres - cs)), 7)


class RecoveryTests(unittest.TestCase):
    def run_check(self, held, slot=1, ready=lambda: True):
        inp, log = Mock(), []
        frames = np.zeros((600, 960, 3), np.uint8)
        reads = [Hotbar(reads=[], held=n) for n in held]
        with patch("fischuse.read_hotbar", side_effect=reads), \
                patch("fischequip.find_hotbar_slot", return_value=(slot, "named slot")):
            outcome = fe.ensure_rod_held(lambda: frames, "Duskwire", [], [], inp, ready, log.append)
        return outcome, inp, log

    def test_already_held_never_toggles_it_away(self):
        outcome, inp, _ = self.run_check([1])
        self.assertEqual(outcome, "held")
        inp.tap.assert_not_called()

    def test_unequipped_restores_once_and_verifies(self):
        outcome, inp, _ = self.run_check([None, 1])
        self.assertEqual(outcome, "restored")
        inp.tap.assert_called_once_with(0x31)

    def test_unreadable_rod_does_not_guess_a_number(self):
        outcome, inp, _ = self.run_check([None], slot=None)
        self.assertEqual(outcome, "unknown")
        inp.tap.assert_not_called()

    def test_unreadable_held_frame_after_a_press_never_presses_twice(self):
        # Live 2026-10-05: "did not return to hand" every 5s toggled the rod
        # in and out. No held frame anywhere after the press -> unverified.
        outcome, inp, _ = self.run_check([None, None])
        self.assertEqual(outcome, "unverified")
        inp.tap.assert_called_once()

    def test_recovery_failure_and_focus_loss(self):
        outcome, inp, _ = self.run_check([2, 2])
        self.assertEqual(outcome, "failed")
        inp.tap.assert_called_once()
        calls = iter([True, False])
        outcome, inp, _ = self.run_check([2], ready=lambda: next(calls))
        self.assertEqual(outcome, "cancelled")
        inp.tap.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def bot(self):
        b = FischBot.__new__(FischBot)
        b.running = True
        b.mouse = SimpleNamespace(dry_run=False, release=Mock())
        b.focus = SimpleNamespace(ready=lambda: True)
        b.log = Mock()
        b.grabber = SimpleNamespace(grab=Mock(), reframe=Mock())
        b.rod = SimpleNamespace(name="Duskwire")
        b.owned_rods, b.enchants = ["Duskwire", "Fabulous Rod"], []
        b._next_rod_check, b._rod_check_failed = 0., False
        return b

    def test_two_minute_schedule_and_failed_recovery_delays_casts(self):
        b = self.bot()
        with patch("fischbot.time.monotonic", return_value=0.) as clock, \
                patch("fischequip.ensure_rod_held", return_value="held") as check, \
                patch("fischequip.WinInput"):
            self.assertTrue(b._check_rod())
            clock.return_value = 119.
            self.assertTrue(b._check_rod())
            self.assertEqual(check.call_count, 1)
            clock.return_value = 120.
            check.return_value = "failed"
            self.assertFalse(b._check_rod())
            clock.return_value = 121.
            self.assertFalse(b._check_rod())
            self.assertEqual(check.call_count, 2)
            clock.return_value = 125.
            check.return_value = "restored"
            self.assertTrue(b._check_rod())

    def test_unverified_press_lets_the_bot_cast(self):
        b = self.bot()
        with patch("fischbot.time.monotonic", return_value=0.),                 patch("fischequip.ensure_rod_held", return_value="unverified") as check,                 patch("fischequip.WinInput"):
            self.assertTrue(b._check_rod())          # cast; the reel decides
            self.assertTrue(b._rod_unverified)
            self.assertTrue(b._check_rod())          # and no second press in between
            self.assertEqual(check.call_count, 1)

    def test_resize_updates_capture_bounds_and_invalidates_reel_geometry(self):
        b = self.bot()
        b.hwnd, b.rect = 42, WinRect(0, 0, 1920, 1009)
        new = WinRect(100, 50, 952, 1050)
        b.pending_hint, b.last_prog_top, b.slider_w = object(), 900, 252
        b._slider_widths, b.ctl, b.recentre = [252], Mock(), Mock()
        with patch("fastcap.client_rect", return_value=new), patch("fischbot.set_client") as scale:
            self.assertTrue(b._refresh_window())
            b.grabber.reframe.assert_called_once_with(new)
            self.assertEqual(b.rect, new)
            scale.assert_called_once_with(852, 1000)
            self.assertIsNone(b.pending_hint)
            self.assertIsNone(b.last_prog_top)
            b.ctl.reset_motion.assert_called_once()

    def test_verified_equip_clears_failed_recovery_and_never_toggles_held_rod(self):
        b = self.bot()
        b.rect, b.state, b.recentre = WinRect(0, 0, 852, 1000), "idle", Mock()
        b._rod_check_failed = True
        menu, inp = Mock(), Mock()
        menu.__enter__ = Mock(return_value=menu)
        menu.__exit__ = Mock(return_value=False)
        menu.equip.return_value = "already"
        with patch.object(b, "_refresh_window", return_value=True), \
                patch("fischequip.EquipmentMenu", return_value=menu), \
                patch("fischequip.WinInput", return_value=inp), \
                patch("fischequip.ensure_rod_held", return_value="held"), \
                patch("fischbot.time.sleep"):
            self.assertTrue(b.equip_rod("Duskwire"))
            self.assertFalse(b._rod_check_failed)
            inp.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
