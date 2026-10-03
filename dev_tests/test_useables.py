"""Useables (fischuse.py): general config, screen reads, limits, totem/bait use."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischequip as fe  # noqa: E402
import fischuse as fu  # noqa: E402

HOTBAR = ROOT / "dev_tests" / "fixtures" / "hotbar_default_font.png"


def _hotbar_frame():
    import cv2
    im = cv2.imread(str(HOTBAR))[:, :, ::-1]
    frame = np.zeros((1009, 1920, 3), np.uint8)
    frame[1009 - im.shape[0]:, 589:589 + im.shape[1]] = im      # centred like the game
    return frame


class ConfigTests(unittest.TestCase):
    def test_clean_and_separate_file(self):
        u = fu.clean_useables({"enabled": 1, "totems": [
            {"name": "Mutation Totem", "when": "bogus", "every_min": -5, "max_uses": "3"},
            {"name": "Mutation Totem"}, {"nope": 1}],
            "bait": {"manage": True, "when_out": "x", "list": [{"name": "Cupcakes", "keep": 20}]}})
        self.assertTrue(u["enabled"])
        self.assertEqual(len(u["totems"]), 1)                  # duplicate + junk dropped
        t = u["totems"][0]
        self.assertEqual((t["when"], t["every_min"], t["max_uses"]), ("every", 1, 3))
        self.assertEqual(u["bait"]["when_out"], "stop")
        self.assertEqual(u["bait"]["list"][0]["keep"], 20)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "general.json"
            fu.save_general({"useables": u, "other": 1}, p)
            back = fu.load_general(p)
            self.assertEqual(back["useables"], u)
            self.assertEqual(back["other"], 1)
        # not part of the hot-swap setups
        import fischui
        self.assertNotIn("useables", fischui.DEFAULTS)


class ReadTests(unittest.TestCase):
    def test_bait_line_votes_across_garbled_reads(self):
        reads = ["CyrrentBait.:", "[x179]", "SCurrentBait.: Cupcakes[kl 79]",
                 "agirqentBait.: gupqakes[kl 79]", "rgirrsntBa/t.� Cupcakes]><179]"]
        self.assertEqual(fu.parse_bait_line(reads, ["Cupcakes", "Instant Catcher"]),
                         ("Cupcakes", 179))
        self.assertEqual(fu._count("x92"), 92)
        self.assertEqual(fu._count("[kl 79]"), 179)

    def test_real_hotbar(self):
        if not HOTBAR.exists():
            self.skipTest("local hotbar fixture not present")
        frame = _hotbar_frame()
        self.assertEqual(fu.read_current_bait(frame), ("Cupcakes", 179))
        hb = fu.read_hotbar(frame)
        self.assertEqual(hb.counts, {9: 92})                    # Mutation Totem x92
        self.assertEqual(hb.held, 1)                            # the rod's white frame
        f2 = frame.copy()
        f2[1009 - 139 + 60:, 589 + 60:589 + 64] = 30            # frame's left side gone
        self.assertIsNone(fu.read_hotbar(f2).held)


def _bot(log):
    b = SimpleNamespace(quests=[], running=True, stopped=False,
                        grabber=SimpleNamespace(grab=lambda: np.zeros((1009, 1920, 3), np.uint8)),
                        rect=SimpleNamespace(left=0, top=0, width=1920, height=1009),
                        recentre=lambda: None, log=log)
    b.stop = lambda: setattr(b, "stopped", True)
    b.focus = SimpleNamespace(ready=lambda: True)
    b.mouse = SimpleNamespace(dry_run=False)
    # Scheduler tests start with a freshly verified, ordinary weather HUD.
    from fischweather import WeatherReader, WeatherState
    b.weather = WeatherReader(clock=lambda: 1000.0)
    b.weather.state = WeatherState(("Rain", "Day", "Spring"), True, 1000.0)
    b.weather.refresh = lambda bot, confirm=False: setattr(
        b.weather, "state", WeatherState(("Rain", "Day", "Spring"), True, 1000.0))
    return b


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LimitTests(unittest.TestCase):
    def test_totem_triggers_and_limits(self):
        clock, logs = Clock(), []
        u = fu.Useables({"enabled": True, "totems": [
            {"name": "Mutation Totem", "when": "every", "every_min": 10, "max_uses": 2, "keep": 5}]},
            logs.append, clock)
        t = u.cfg["totems"][0]
        self.assertTrue(u.totem_due(t, [], clock()))
        used = []

        def fake_use(bot, item):
            used.append(clock())
            u.st(item["name"]).uses += 1
            u.st(item["name"]).last_used = clock()
            return True
        with patch.object(u, "use_totem", side_effect=fake_use):
            bot = _bot(logs.append)
            u.between_casts(bot)
            u.between_casts(bot)                      # within 10 min: not again
            clock.t += 601
            u.between_casts(bot)
            clock.t += 601
            u.between_casts(bot)                      # max 2 per run
        self.assertEqual(len(used), 2)
        self.assertIn("limit reached (2/2", u.st("Mutation Totem").status)
        # keep: at or below the reserve, not used; more again -> resumes
        u2 = fu.Useables({"enabled": True, "totems": [{"name": "Aurora Totem", "keep": 5}]},
                         logs.append, clock)
        u2.st("Aurora Totem").count = 5
        self.assertIn("keeping 5", u2.limited(u2.cfg["totems"][0]))
        u2.st("Aurora Totem").count = 9
        self.assertIsNone(u2.limited(u2.cfg["totems"][0]))

    def test_quest_trigger_uses_the_totems_weather(self):
        from fischquest import Objective, Quest
        clock = Clock()
        u = fu.Useables({"enabled": True, "totems": [
            {"name": "Mutation Totem", "when": "quest"}, {"name": "Aurora Totem", "when": "quest"}]},
            lambda m: None, clock)
        q = Quest(title="x", objectives=[Objective(text="Catch 1 Gusty Abaia", mutations=["Gusty"])])
        mt, at = u.cfg["totems"]
        self.assertIn("Gusty", u.totem_due(mt, [q], clock()))   # Mutation Totem: any mutation
        self.assertIsNone(u.totem_due(at, [q], clock()))         # Aurora weather doesn't give Gusty
        self.assertIsNone(u.totem_due(mt, [], clock()))

    def test_bait_order_reserve_and_stop(self):
        clock, logs = Clock(), []
        u = fu.Useables({"enabled": True, "bait": {"manage": True, "list": [
            {"name": "Cupcakes", "keep": 100}, {"name": "Luminous Larva", "max_uses": 2}]}},
            logs.append, clock)
        bot = _bot(logs.append)
        equipped = []

        def fake_equip(bot, b):
            equipped.append(b["name"])
            u.current_bait = b["name"]
            return True
        reads = iter([("Cupcakes", 179), ("Cupcakes", 100), ("Luminous Larva", 50),
                      ("Luminous Larva", 49), ("Luminous Larva", 48)])
        with patch.object(u, "equip_bait", side_effect=fake_equip), \
                patch("fischuse.read_current_bait", side_effect=lambda f: next(reads)):
            u.between_casts(bot)                   # Cupcakes 179 > 100: keep it
            self.assertEqual(equipped, [])
            u.cast_done()
            u.between_casts(bot)                   # 100 left = keep -> next bait
            self.assertEqual(equipped, ["Luminous Larva"])
            for _ in range(2):
                u.cast_done()
                u.between_casts(bot)
            self.assertTrue(bot.stopped)            # Larva hit 2 casts; Cupcakes at reserve
        self.assertIn("every bait in your list is at its limit", "\n".join(logs))


class TotemUseTests(unittest.TestCase):
    def test_no_click_unless_the_totem_is_in_hand(self):
        taps, clicks, logs = [], [], []
        inp = SimpleNamespace(tap=lambda vk, mods=(): taps.append(vk),
                              click=lambda x, y: clicks.append((x, y)))
        u = fu.Useables({"enabled": True, "totems": [{"name": "Mutation Totem"}]}, logs.append)
        t = u.cfg["totems"][0]
        hb = lambda held, n: fu.Hotbar(reads=[[("x", (0, 0, 1, 1))]], centres=np.arange(9) * 69 + 684.0,
                                       pitch=69, counts={9: n}, held=held)
        bot = _bot(logs.append)
        with patch("fischequip.WinInput", return_value=inp), patch("fischuse.time.sleep"), \
                patch("fischequip.find_hotbar_slot", return_value=(9, "slot 9")), \
                patch("fischuse.read_hotbar", side_effect=[hb(1, 92), hb(1, 92), hb(1, 92)]):
            self.assertFalse(u.use_totem(bot, t))       # pressing 9 didn't change the frame
        self.assertEqual(clicks, [])
        self.assertEqual(taps[0], 0x39)
        self.assertEqual(taps[-1], fe.VK_T)
        taps.clear()
        with patch("fischequip.WinInput", return_value=inp), patch("fischuse.time.sleep"), \
                patch("fischequip.find_hotbar_slot", return_value=(9, "slot 9")), \
                patch("fischuse.read_hotbar", side_effect=[hb(1, 92), hb(9, 92), hb(9, 91), hb(1, 91)]):
            self.assertTrue(u.use_totem(bot, t))
        self.assertEqual(len(clicks), 1)
        self.assertEqual(taps, [0x39, fe.VK_T])
        s = u.st("Mutation Totem")
        self.assertEqual((s.uses, s.count), (1, 91))
        self.assertIn("92 -> 91 left", "\n".join(logs))


class BaitMenuTests(unittest.TestCase):
    def test_equip_bait_in_the_side_panel(self):
        class Game:
            def __init__(self):
                self.typed, self.equipped, self.tab = "", False, False

            def lines(self):
                L = [("Search...", (300, 100, 600, 130)), ("[Fabulous Rod]", (320, 400, 480, 420)),
                     ("Equipped", (360, 440, 440, 460)), ("Baits", (1270, 40, 1320, 60)),
                     ("Search Baits...", (1260, 80, 1450, 110))]
                if self.typed == "Luminous Larva":
                    L += [("[Luminous Larva]", (1290, 300, 1420, 320)), ("x2573", (1330, 325, 1380, 340)),
                          ("[Equipped]" if self.equipped else "[Equip]", (1320, 360, 1400, 380))]
                return L

        g = Game()
        clicked = []

        def click(b):
            clicked.append(b)
            if b == (1320, 360, 1400, 380):
                g.equipped = True

        menu = fe.EquipmentMenu(lambda: None, SimpleNamespace(left=0, top=0), lambda m: None,
                                inp=SimpleNamespace(tap=lambda *a: None, type_text=lambda t: None),
                                reader=lambda f: fe.Screen(g.lines(), [], fe.find_search(g.lines())))
        menu.click = click
        menu.set_search = lambda text: (setattr(g, "typed", text), menu.read())[1]
        with patch("fischequip.time.sleep"):
            self.assertEqual(menu.equip_bait("Luminous Larva"), ("equipped", 2573))
        self.assertIn((1270, 40, 1320, 60), clicked)            # the Baits tab
        self.assertEqual(menu.search_box, (1260, 80, 1450, 110))  # the bait box, not the rods'
        g.equipped = False
        with patch("fischequip.time.sleep"):
            self.assertEqual(menu.equip_bait("Luminous Larva", keep=3000), ("reserve", 2573))


if __name__ == "__main__":
    unittest.main()
