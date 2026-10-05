"""Misc > Lullaby buffs: the mode buttons on the bag card, the schedule, settings."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischequip as fe  # noqa: E402
import fischlullaby as fl  # noqa: E402
import fischuse as fu  # noqa: E402

# The user's screenshot of the Lullaby's card edge in the bag (2026-10-05): the
# six mode buttons, top to bottom. No names or other players on it.
COLUMN = ROOT / "dev_tests" / "lullaby_bag_column.png"
TOPS = (11, 39, 66, 95, 122, 151)       # button centres in the screenshot (y)
X = 24                                   # ... (x)


def card_frame(scale=1.0, at=(560, 150), noise=6):
    col = cv2.imread(str(COLUMN))[:, :, ::-1]
    col = cv2.resize(col, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    f = np.full((720, 1280, 3), (18, 14, 32), np.uint8)
    f[at[1]:at[1] + col.shape[0], at[0]:at[0] + col.shape[1]] = col
    rng = np.random.default_rng(0)
    f = np.clip(f.astype(int) + rng.integers(-noise, noise + 1, f.shape), 0, 255)
    return f.astype(np.uint8)


class ButtonTests(unittest.TestCase):
    def test_finds_the_six_buttons_at_several_window_sizes(self):
        for scale in (0.6, 1.0, 1.4):
            found = fe.find_lullaby_modes(card_frame(scale), (330, 40, 640, 680))
            self.assertIsNotNone(found, scale)
            boxes, score = found
            self.assertEqual(len(boxes), 6)
            for b, y in zip(boxes, TOPS):
                cx, cy = fe.centre(b)
                self.assertLess(abs(cx - (560 + X * scale)), 3, scale)
                self.assertLess(abs(cy - (150 + y * scale)), 3, scale)

    def test_nothing_on_a_card_without_them(self):
        f = card_frame()
        f[:, 540:640] = (18, 14, 32)
        self.assertIsNone(fe.find_lullaby_modes(f, (330, 40, 640, 680)))
        noise = np.random.default_rng(1).integers(0, 255, (720, 1280, 3)).astype(np.uint8)
        self.assertIsNone(fe.find_lullaby_modes(noise, (330, 40, 640, 680)))


class FakeBag:
    """The rod screen with the Lullaby's card; records clicks."""

    def __init__(self):
        self.clicks = []
        self.frame = card_frame()

    def click(self, x, y):
        self.clicks.append((x, y))
        self.frame = self.frame.copy()
        self.frame[y - 6:y + 6, x - 6:x + 6] = 255            # the button lights up

    def tap(self, *a):
        pass

    def read(self, _frame):
        card = {"rod": "Lullaby", "enchants": [], "equipped": True,
                "box": (330, 0, 600, 620), "name_box": (420, 600, 520, 620), "bottom": 700}
        return fe.Screen([("[Lullaby]", card["name_box"])], [card], None)


class BagTests(unittest.TestCase):
    def menu(self, bag):
        rect = SimpleNamespace(left=0, top=0)
        m = fe.EquipmentMenu(lambda: bag.frame, rect, lambda _: None, inp=bag, reader=bag.read)
        m._pause = lambda s: None
        return m

    def test_presses_the_button_of_the_mode(self):
        bag = FakeBag()
        self.assertEqual(self.menu(bag).lullaby_mode(3), "changed")   # Fortuitous, green
        x, y = bag.clicks[-1]
        self.assertLess(abs(x - (560 + X)), 3)
        self.assertLess(abs(y - (150 + TOPS[3])), 3)

    def test_no_card_or_no_buttons_is_an_error(self):
        bag = FakeBag()
        bag.frame = np.full_like(bag.frame, 20)
        with self.assertRaises(fe.MenuError):
            self.menu(bag).lullaby_mode(0)
        bag = FakeBag()
        bag.read = lambda _f: fe.Screen([], [], None)
        with self.assertRaises(fe.MenuError):
            self.menu(bag).lullaby_mode(0)


class SettingsTests(unittest.TestCase):
    def test_clean(self):
        c = fl.clean_lullaby({"enabled": 1, "steps": [
            {"mode": "Fortuitous Harmony", "minutes": "20"}, {"mode": "Bogus"},
            {"mode": "Serene Hymn", "minutes": 9999}, "junk"]})
        self.assertEqual(c, {"enabled": True, "steps": [
            {"mode": "Fortuitous Harmony", "minutes": 20}, {"mode": "Serene Hymn", "minutes": 600}]})
        self.assertEqual(fl.clean_lullaby(None), {"enabled": False, "steps": []})
        self.assertEqual(len(fl.MODES), 6)

    def test_kept_in_the_general_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "general.json"
            fu.save_general({"useables": {"enabled": True}}, p)
            fu.save_general({"lullaby": {"enabled": True, "steps": [{"mode": "Prismatic Sinfonia"}]}}, p)
            g = fu.load_general(p)
            self.assertTrue(g["useables"]["enabled"])                   # untouched
            self.assertEqual(g["lullaby"]["steps"], [{"mode": "Prismatic Sinfonia", "minutes": 15}])


def bot(rod="Lullaby"):
    return SimpleNamespace(rod=SimpleNamespace(name=rod))


class ScheduleTests(unittest.TestCase):
    def make(self, steps, results=None):
        self.now = 1000.0
        self.logs = []
        lb = fl.LullabyBuffs({"enabled": True, "steps": steps}, self.logs.append,
                             clock=lambda: self.now)
        self.pressed = []
        results = list(results or [])

        def press(_bot, i):
            self.pressed.append(fl.MODES[i]["buff"])
            return results.pop(0) if results else "changed"
        lb.press = press
        return lb

    def test_switches_after_each_buffs_time_and_starts_over(self):
        lb = self.make([{"mode": "Fortuitous Harmony", "minutes": 10},
                        {"mode": "Prismatic Sinfonia", "minutes": 5}])
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous"])                # the first at the start
        self.now += 9 * 60
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous"])                # not yet
        self.assertIn("1 min, then Prismatic", lb.status)
        self.now += 61
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous", "Prismatic"])
        self.now += 5 * 60
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous", "Prismatic", "Fortuitous"])   # round again
        self.assertEqual(lb.view()["switches"], 3)

    def test_one_buff_stays_on(self):
        lb = self.make([{"mode": "Serene Hymn", "minutes": 1}])
        lb.between_casts(bot())
        self.now += 3600
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Serenity"])

    def test_only_with_the_lullaby_and_retries_after_a_failure(self):
        lb = self.make([{"mode": "Quickening Symphony", "minutes": 5}], ["error no mode buttons"])
        lb.between_casts(bot("Fabulous Rod"))
        self.assertEqual(self.pressed, [])
        self.assertIn("Fabulous Rod", lb.status)
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Quickening"])
        self.assertIn("could not set", lb.status)
        lb.between_casts(bot())
        self.assertEqual(len(self.pressed), 1)                        # waits before retrying
        self.now += fl.RETRY_S
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Quickening", "Quickening"])
        self.assertEqual(lb.mode, "Quickening Symphony")

    def test_not_attempted_is_not_a_failure(self):
        lb = self.make([{"mode": "Quickening Symphony", "minutes": 5}], [None])
        lb.between_casts(bot())
        lb.between_casts(bot())
        self.assertEqual(len(self.pressed), 2)

    def test_editing_keeps_the_current_buff_and_skip_moves_on(self):
        lb = self.make([{"mode": "Fortuitous Harmony", "minutes": 10},
                        {"mode": "Prismatic Sinfonia", "minutes": 5}])
        lb.between_casts(bot())
        self.now += 60
        lb.update({"enabled": True, "steps": [{"mode": "Resistant Composition", "minutes": 5},
                                              {"mode": "Fortuitous Harmony", "minutes": 30}]})
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous"])                # still on it, now step 2
        self.assertEqual(lb.index, 1)
        lb.skip()
        lb.between_casts(bot())
        self.assertEqual(self.pressed, ["Fortuitous", "Resistant"])
        lb.update({"enabled": True, "steps": [{"mode": "Serene Hymn", "minutes": 5}]})
        lb.between_casts(bot())
        self.assertEqual(self.pressed[-1], "Serenity")                # removed: first step

    def test_off_does_nothing(self):
        lb = self.make([{"mode": "Serene Hymn", "minutes": 5}])
        lb.update({"enabled": False, "steps": [{"mode": "Serene Hymn", "minutes": 5}]})
        self.assertFalse(lb.active)


class PressTests(unittest.TestCase):
    """LullabyBuffs.press drives the bag and leaves the rod in hand."""

    def test_press_opens_the_bag_and_checks_the_rod(self):
        calls = []

        class Menu:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                calls.append("open")
                return self

            def __exit__(self, *a):
                calls.append("close")

            def lullaby_mode(self, i, rod):
                calls.append(("mode", i, rod))
                return "changed"

        b = SimpleNamespace(running=True, focus=SimpleNamespace(ready=lambda: True),
                            _refresh_window=lambda: True, mouse=SimpleNamespace(dry_run=False, release=lambda: None),
                            state="x", grabber=SimpleNamespace(grab=lambda: None), rect=None,
                            rod=SimpleNamespace(name="Lullaby"), enchants=[], _input_ready=lambda: True,
                            recentre=lambda: calls.append("recentre"))
        held = lambda *a, **k: calls.append("held") or "held"
        with patch.object(fe, "EquipmentMenu", Menu), patch.object(fe, "ensure_rod_held", held), \
                patch("time.sleep", lambda s: None):
            r = fl.LullabyBuffs({"enabled": True, "steps": []}, lambda _: None).press(b, 4)
        self.assertEqual(r, "changed")
        self.assertEqual(calls, ["open", ("mode", 4, "Lullaby"), "close", "held", "recentre"])
        self.assertEqual(b.state, "x")


if __name__ == "__main__":
    unittest.main()
