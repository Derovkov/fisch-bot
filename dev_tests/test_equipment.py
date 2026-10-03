"""fischequip against a simulated Equipment Bag (no Roblox, no real input).

The fake game follows what the user described: N toggles the bag (not while the
rod is cast), it opens on the rod section, it has a search box, each rod card
has an Equip / Equipped button under its [Name]. The reader returns what OCR
would: text lines with boxes, plus fischscan-style cards."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischequip as fe  # noqa: E402

SEARCH_BOX = (100, 40, 260, 70)
CARD_W, PER_SCREEN = 200, 4


class FakeGame:
    def __init__(self, owned, equipped=None, cast=False, show_search=True):
        self.owned = dict(owned)                # rod -> enchants
        self.equipped = equipped
        self.cast = cast
        self.show_search = show_search
        self.open = False
        self.query = ""
        self.focused = False
        self.scroll = 0
        self.keys = []

    # -- input -----------------------------------------------------------------
    def tap(self, vk, mods=()):
        self.keys.append((vk, mods))
        if self.focused:
            if vk == fe.VK_A and fe.VK_CONTROL in mods:
                self._select_all = True
            elif vk == fe.VK_BACK:
                self.query = "" if getattr(self, "_select_all", False) else self.query[:-1]
                self._select_all = False
            elif vk == fe.VK_RETURN:
                self.focused = False
            return
        if vk == fe.VK_N and not self.cast:
            self.open = not self.open

    def type_text(self, text):
        if self.focused:
            self.query += text
        elif "n" in text.lower() and not self.cast:
            self.open = not self.open           # typed into the game: chaos

    def click(self, x, y):
        if not self.open:
            return
        if self.show_search and SEARCH_BOX[0] <= x <= SEARCH_BOX[2] and SEARCH_BOX[1] <= y <= SEARCH_BOX[3]:
            self.focused = True
            return
        self.focused = False
        for rod, x0 in self._visible():
            if x0 <= x <= x0 + CARD_W and 430 <= y <= 450:
                self.equipped = rod

    def wheel(self, x, y, notches):
        self.scroll = max(0, min(len(self.owned) - 1, self.scroll + notches))

    # -- what OCR would read ------------------------------------------------------
    def _visible(self):
        rods = sorted(r for r in self.owned if self.query.lower() in r.lower())
        if not self.query:
            rods = rods[self.scroll:self.scroll + PER_SCREEN]
        return [(r, 300 + i * CARD_W) for i, r in enumerate(rods[:PER_SCREEN])]

    def screen(self, _frame=None):
        if not self.open:
            return fe.Screen([("Level 52", (10, 10, 60, 20))], [], None)
        lines, cards = [], []
        search = None
        if self.show_search:
            text = self.query or "Search..."
            lines.append((text, SEARCH_BOX))
            search = SEARCH_BOX if not self.query else None
        for rod, x0 in self._visible():
            eq = rod == self.equipped
            lines.append((f"[{rod}]", (x0 + 20, 390, x0 + 180, 410)))
            lines.append(("Equipped" if eq else "Equip", (x0 + 60, 430, x0 + 140, 450)))
            cards.append({"rod": rod, "enchants": list(self.owned[rod]), "equipped": eq,
                          "box": (x0, 0, x0 + CARD_W, 410)})
        return fe.Screen(lines, cards, search)


def menu_for(game):
    return fe.EquipmentMenu(lambda: None, SimpleNamespace(left=0, top=0), lambda m: None,
                            inp=game, reader=game.screen)


class EquipmentMenuTests(unittest.TestCase):
    def setUp(self):
        for name in ("OPEN_WAIT_S", "CLOSE_WAIT_S"):
            p = patch.object(fe, name, 0.05)
            p.start()
            self.addCleanup(p.stop)
        for name in ("SEARCH_SETTLE_S", "EQUIP_SETTLE_S", "SCROLL_SETTLE_S"):
            p = patch.object(fe, name, 0)
            p.start()
            self.addCleanup(p.stop)

    def test_equip_opens_searches_equips_verifies_and_closes(self):
        g = FakeGame({"Duskwire": [], "Crew Rod": [], "Fabulous Rod": ["Crested"]},
                     equipped="Fabulous Rod")
        with menu_for(g) as m:
            self.assertEqual(m.equip("Duskwire"), "equipped")
        self.assertEqual(g.equipped, "Duskwire")
        self.assertFalse(g.open)
        self.assertEqual(g.query, "")              # search cleared for next time
        self.assertFalse(g.focused)

    def test_already_equipped_clicks_nothing(self):
        g = FakeGame({"Duskwire": []}, equipped="Duskwire")
        with menu_for(g) as m:
            self.assertEqual(m.equip("Duskwire"), "already")
        self.assertFalse(g.open)

    def test_bag_will_not_open_while_cast(self):
        g = FakeGame({"Duskwire": []}, cast=True)
        with self.assertRaises(fe.MenuError):
            with menu_for(g) as m:
                m.equip("Duskwire")
        self.assertFalse(g.open)

    def test_missing_rod_raises_and_still_closes_the_bag(self):
        g = FakeGame({"Duskwire": []})
        with self.assertRaises(fe.MenuError):
            with menu_for(g) as m:
                m.equip("Crew Rod")
        self.assertFalse(g.open)
        self.assertEqual(g.query, "")

    def test_search_scan_finds_every_owned_rod_with_enchants(self):
        owned = {f"Rod {c}": ([f"E{c}"] if c in "AC" else []) for c in "ABCDEFGHIJ"}
        g = FakeGame(owned, equipped="Rod C")
        known = sorted(owned) + ["Not Owned Rod", "Another Missing"]
        with menu_for(g) as m:
            found = m.scan_by_search(known)
        self.assertEqual(set(found), set(owned))
        self.assertEqual(found["Rod A"]["enchants"], ["EA"])
        self.assertTrue(found["Rod C"]["equipped"])
        self.assertFalse(g.open)

    def test_scroll_scan_reads_every_screen_until_the_list_stops_moving(self):
        owned = {f"Rod {i:02d}": [] for i in range(40)}
        g = FakeGame(owned)
        g.scroll = 17                              # starts mid-list: must go to the top
        with menu_for(g) as m:
            found = m.scan_by_scroll()
        self.assertEqual(set(found), set(owned))
        self.assertFalse(g.open)

    def test_scroll_scan_keeps_going_when_a_scroll_shows_no_new_rod(self):
        # One rod per notch, 1 notch per step: most screens add nothing new on
        # their own two reads in a row -- the old "no new rod twice" rule quit.
        owned = {f"Rod {i:02d}": [] for i in range(30)}
        g = FakeGame(owned)
        with patch.object(fe, "SCROLL_NOTCHES", 1):
            with menu_for(g) as m:
                found = m.scan_by_scroll()
        self.assertEqual(set(found), set(owned))

    def test_scroll_retries_over_another_card_before_calling_it_the_end(self):
        owned = {f"Rod {i:02d}": [] for i in range(12)}
        g = FakeGame(owned)
        wheel = g.wheel
        spots = []

        def picky_wheel(x, y, n):                  # the first spot is outside the list
            spots.append((x, y))
            if x == 300 + CARD_W // 2:             # over the first card only
                wheel(x, y, n)
        g.wheel = picky_wheel
        with menu_for(g) as m:
            found = m.scan_by_scroll()
        self.assertEqual(set(found), set(owned))

    def test_cancel_stops_a_search_scan(self):
        g = FakeGame({"Rod A": [], "Rod B": []})
        calls = []
        m = fe.EquipmentMenu(lambda: None, SimpleNamespace(left=0, top=0), lambda s: None,
                             inp=g, reader=g.screen, cancelled=lambda: len(calls) >= 1)
        with m:
            found = m.scan_by_search(["Rod A", "Rod B"], lambda *a: calls.append(a))
        self.assertEqual(list(found), ["Rod A"])

    def test_button_ignores_other_cards_and_text_above_the_name(self):
        s = fe.Screen([("[Duskwire]", (20, 390, 180, 410)), ("Equip", (60, 430, 140, 450)),
                       ("Equipped", (260, 430, 340, 450)), ("Equip", (60, 300, 140, 320))],
                      [{"rod": "Duskwire", "enchants": [], "equipped": False,
                        "box": (0, 0, 200, 410)}], None)
        kind, box = s.button(s.cards[0])
        self.assertEqual((kind, box), ("equip", (60, 430, 140, 450)))

    @staticmethod
    def _hotbar_words(labels, width=1920, pitch=69, y=200):
        """OCR-like words for a centred hotbar: labels[i] = slot i+1's text,
        one line per element; plus a catch caption above the row."""
        n = len(labels)
        words = [("You", (900, 20, 930, 35)), ("caught", (935, 20, 990, 35)),
                 ("Equipped", (900, 50, 960, 65)), ("Fabulous", (965, 50, 1030, 65))]
        for i, lab in enumerate(labels):
            cx = width / 2 + pitch * (i + 1 - (n + 1) / 2)
            for j, line in enumerate(lab):
                w = 7 * len(line)
                words.append((line, (int(cx - w / 2), y + 13 * j, int(cx + w / 2), y + 13 * j + 11)))
        return words

    def test_hotbar_slot_by_geometry_and_name_with_enchant_and_garbling(self):
        rods = ["Fabulous Rod", "Duskwire", "Crew Rod", "Volcanic Rod", "Requiem", "Spirit of the Forest"]
        labels = [["Equipment", "Bag"], ["Starforged", "Spirit", "F abvtovs", "Rod"],
                  ["Bestiary"], ["Velocity", "cod"], ["Pogo Stick"], ["Tidebreaker"],
                  ["Traveler's", "Whistle"], ["Quest", "Book"], ["Mutation", "Totem"]]
        ench = ["Starforged Spirit", "Crested", "Piercing"]
        reads = [self._hotbar_words(labels)]
        self.assertEqual(fe.find_hotbar_slot(reads, 1920, "Fabulous Rod", rods, ench)[0], 2)
        # garbled items must not pass for rods that are not in the hotbar
        for rod in ("Crew Rod", "Volcanic Rod", "Requiem", "Duskwire"):
            self.assertIsNone(fe.find_hotbar_slot(reads, 1920, rod, rods, ench)[0], rod)
        labels[1] = ["Piercing", "Duskwire"]
        reads = [self._hotbar_words(labels)]
        self.assertEqual(fe.find_hotbar_slot(reads, 1920, "Duskwire", rods, ench)[0], 2)

    def test_changed_slot_breaks_a_weak_read(self):
        import numpy as np
        rods = ["Fabulous Rod", "Duskwire"]
        labels = [["Equipment"], ["Dvsk"], ["Bestiary"], ["Quest", "Book"]]
        reads = [self._hotbar_words(labels, y=40)]
        self.assertIsNone(fe.find_hotbar_slot(reads, 1920, "Duskwire", rods)[0])
        before = np.zeros((120, 1920, 3), np.uint8)
        after = before.copy()
        cx = int(1920 / 2 + 69 * (2 - 2.5))
        after[30:100, cx - 25:cx + 25] = 200       # slot 2's picture changed
        self.assertEqual(fe.find_hotbar_slot(reads, 1920, "Duskwire", rods, (), before, after)[0], 2)

    def test_real_hotbar_crop_finds_fabulous_rod_in_slot_2(self):
        crop = ROOT / "saved_logs" / "20261003_135047" / "hotbar_0016.25.png"
        if not crop.exists():
            self.skipTest("local hotbar fixture not present")
        import cv2
        from fischrods import ENCHANTS, rod_names
        band = cv2.imread(str(crop))[:, :, ::-1].copy()
        reads = fe.read_hotbar_words(band, 1920)
        names = rod_names()
        self.assertEqual(fe.find_hotbar_slot(reads, 1920, "Fabulous Rod", names, list(ENCHANTS))[0], 2)
        for rod in ("Duskwire", "Crew Rod", "Volcanic Rod", "Requiem", "Dreambreaker"):
            self.assertIsNone(fe.find_hotbar_slot(reads, 1920, rod, names, list(ENCHANTS))[0], rod)

    def test_select_hotbar_presses_the_slot_number(self):
        import numpy as np
        g = FakeGame({})
        labels = [["Equipment"], ["Bestiary"], ["Quest", "Book"], ["Duskwire"]]
        with patch.object(fe, "read_hotbar_words", return_value=[self._hotbar_words(labels, y=10)]):
            ok = fe.select_hotbar_rod(lambda: np.zeros((400, 1920, 3), np.uint8), "Duskwire",
                                      ["Duskwire", "Crew Rod"], g, lambda m: None)
        self.assertTrue(ok)
        self.assertEqual(g.keys[-1], (0x34, ()))

    def test_find_search_reads_placeholder_variants(self):
        self.assertEqual(fe.find_search([("Search rods...", (1, 2, 3, 4))]), (1, 2, 3, 4))
        self.assertIsNone(fe.find_search([("Researcher", (1, 2, 3, 4))]))

    def test_side_panel_bait_search_is_ignored(self):
        # The bag's Baits/Bobbers/Lanterns panel has "Search Baits..." too
        # (user screenshot): rod switching must never type into it.
        rods = (300, 120, 700, 150)
        lines = [("Search Baits...", (1260, 80, 1450, 110)), ("Search...", (300, 120, 700, 150))]
        self.assertEqual(fe.find_search(lines), rods)
        for other in ("Search Balts", "Search Bobbers...", "Search Lanterns..."):
            self.assertIsNone(fe.find_search([(other, (1, 2, 3, 4))]), other)
        self.assertEqual(fe.find_search([("Search Rods...", (1, 2, 3, 4))]), (1, 2, 3, 4))
        # two plain boxes: the one over the rod cards
        cards = [{"box": (250, 300, 450, 330)}, {"box": (500, 300, 700, 330)}]
        lines = [("Search...", (1260, 80, 1450, 110)), ("Search...", (300, 120, 700, 150))]
        self.assertEqual(fe.find_search(lines, cards), rods)


if __name__ == "__main__":
    unittest.main()
