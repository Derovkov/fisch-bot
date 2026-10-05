"""Default reel skin: pen button -> Default [Equip] -> [Back], against a fake bag."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischequip as fe  # noqa: E402

PEN = (300, 200, 330, 230)
DEFAULT, EQUIP, BACK = (70, 339, 124, 351), (77, 391, 117, 408), (656, 21, 691, 34)


class FakeSkinBag:
    """rods screen -> (pen) skins screen -> (Equip) Default equipped -> (Back) rods."""

    def __init__(self, on_default=False, back_works=True):
        self.screen, self.on_default, self.back_works = "rods", on_default, back_works
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))
        inside = lambda b: b[0] <= x <= b[2] and b[1] <= y <= b[3]
        if self.screen == "rods" and inside(PEN):
            self.screen = "skins"
        elif self.screen == "skins" and inside(EQUIP):
            self.on_default = True
        elif self.screen == "skins" and inside(BACK) and self.back_works:
            self.screen = "rods"

    def tap(self, *a):
        pass

    def read(self, _frame):
        if self.screen == "rods":
            card = {"rod": "Noiseform", "enchants": [], "equipped": True,
                    "box": (250, 0, 450, 400), "name_box": (300, 380, 400, 400), "bottom": 600}
            return fe.Screen([("[Noiseform]", card["name_box"])], [card], None)
        lines = [("Noiseform Skins", (10, 10, 200, 30)), ("Default", DEFAULT), ("[Back]", BACK),
                 ("Infrared Tunes", (600, 300, 700, 320)), ("[Equipped]", (600, 390, 700, 410))]
        if self.on_default:
            lines[-1] = ("[Equip]", (600, 390, 700, 410))
            lines.append(("[Equipped]", EQUIP))
        return fe.Screen(lines, [], None)


def menu(game):
    rect = type("R", (), {"left": 0, "top": 0})()
    m = fe.EquipmentMenu(lambda: np.zeros((720, 1280, 3), np.uint8), rect, lambda _: None,
                         inp=game, reader=game.read)
    m._pause = lambda s: None
    return m


class SkinBagTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(fe, "find_skin_edit", lambda frame, region: (PEN, 0.9))
        p.start()
        self.addCleanup(p.stop)
        # the small [Equip] text is only read by the close-up pass
        p = patch.object(fe, "skin_button_closeup",
                         lambda frame, d: ("equip", EQUIP))
        p.start()
        self.addCleanup(p.stop)

    def test_equips_default_then_presses_back(self):
        g = FakeSkinBag()
        self.assertEqual(menu(g).default_skin("Noiseform"), "default")
        self.assertTrue(g.on_default)
        self.assertEqual(g.screen, "rods")                 # [Back] pressed
        self.assertEqual(g.clicks[-1], fe.centre(BACK))

    def test_already_default_still_presses_back(self):
        g = FakeSkinBag(on_default=True)
        self.assertEqual(menu(g).default_skin("Noiseform"), "already")
        self.assertEqual(g.screen, "rods")
        self.assertNotIn(fe.centre(EQUIP), g.clicks)

    def test_a_stuck_skin_list_is_an_error_not_a_silent_open_bag(self):
        g = FakeSkinBag(back_works=False)
        with self.assertRaises(fe.MenuError):
            menu(g).default_skin("Noiseform")

    def test_reads_the_real_skin_screen(self):
        lines = [("Noiseform Skins", (8, 8, 150, 30)), ("Default", DEFAULT), ("Ravereign", (200, 339, 290, 351)),
                 ("[Back]", BACK), ("[Equipped]", (590, 391, 690, 408))]
        sk = fe.skin_screen(lines)
        self.assertEqual((sk["default"], sk["back"], sk["button"]), (DEFAULT, BACK, None))  # far [Equipped] is not Default's
        self.assertIsNone(fe.skin_screen([("[Noiseform]", (1, 1, 2, 2))]))               # rod screen


if __name__ == "__main__":
    unittest.main()
