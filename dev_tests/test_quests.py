"""Quest tracker reading/planning (fischquest.py) and the wiki indexes."""
import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischquest as fq  # noqa: E402

FIXTURE = ROOT / "dev_tests" / "fixtures" / "quest_tracker_custom_font.png"


def _synthetic():
    """A tracker image + OCR lines built to the measured colours/indents."""
    img = np.zeros((300, 650, 3), np.uint8)
    lines = []

    def put(text, x, y, rgb):
        w = 7 * len(text)
        img[y:y + 16, x:x + w] = rgb
        lines.append((text, (x, y, x + w, y + 16)))
    put("Aeronaut Vance: An Air-Worthy Vessel", 33, 1, (252, 252, 252))
    put("Catch 1 African Butterflyfish (1/1)", 44, 33, (146, 211, 150))
    put("Catch and return 1 Gusty Abaia (0/1)", 44, 62, (226, 226, 230))
    put("Everturn Forest: Spirit Seeker", 34, 96, (252, 252, 252))
    put("Return a Rotting Sturgeon of any kind to Thalor Virewood.", 19, 129, (190, 190, 190))
    put("Catch and return 1 Rotting Glaciaseer Sturgeon, Floraseer Sturgeon,", 44, 154, (230, 230, 231))
    put("Solarseer Sturgeon, or Umbraleaf Sturgeon (0/1)", 44, 174, (230, 230, 231))
    put("Location: [US] Texas", 14, 210, (40, 40, 40))
    put("x1", 15, 280, (138, 225, 138))
    return lines, img


class TrackerTests(unittest.TestCase):
    def test_structure_progress_wrapping_and_wiki_requirements(self):
        lines, img = _synthetic()
        qs = fq.parse_tracker(lines, img)
        self.assertEqual([q.title for q in qs], ["Aeronaut Vance: An Air-Worthy Vessel",
                                                 "Everturn Forest: Spirit Seeker"])
        a, b = qs
        self.assertEqual([(o.done, o.have, o.need) for o in a.objectives],
                         [(True, 1, 1), (False, 0, 1)])
        self.assertEqual(a.objectives[1].mutations, ["Gusty"])
        self.assertEqual(a.objectives[1].items, ["Abaia"])
        self.assertEqual((a.npc, a.location), ("Aeronaut Vance", "Skycrest"))
        self.assertIn("Rotting Sturgeon", b.description)
        self.assertEqual(len(b.objectives), 1)               # wrapped line joined
        self.assertEqual(b.objectives[0].mutations, ["Rotting"])
        self.assertIn("Umbraleaf Sturgeon", b.objectives[0].items)

    def test_plan_names_owned_rods_or_other_sources(self):
        lines, img = _synthetic()
        qs = fq.parse_tracker(lines, img)
        text = " | ".join(fq.plan(qs, ["Duskwire"]))
        self.assertIn("needs Gusty", text)
        self.assertIn("Breeze Caster", text)                  # the rod that gives Gusty
        self.assertIn("needs Rotting", text)
        # owning the rod: suggested with its chance
        mine = fq.plan(qs, ["Breeze Caster"])
        self.assertTrue(any("use Breeze Caster" in l for l in mine))

    def test_real_tracker_screenshot(self):
        if not FIXTURE.exists():
            self.skipTest("local tracker fixture not present")
        import cv2
        crop = cv2.imread(str(FIXTURE))[:, :, ::-1]
        canvas = np.zeros((crop.shape[0], 1920, 3), np.uint8)
        canvas[:, :crop.shape[1]] = crop
        qs = fq.read_tracker(canvas)
        self.assertEqual(len(qs), 8)
        by = {q.title: q for q in qs}
        self.assertIn("Everturn Forest: Spirit Seeker", by)   # OCR "Evertum" corrected
        v = by["Aeronaut Vance: An Air-Worthy Vessel"]
        self.assertEqual([o.done for o in v.objectives], [True, True, False, False])
        self.assertTrue(by["Aero: Swift Winds"].done)
        torin = by["Forgemaster Torin: The Empyrean Idol"]
        self.assertEqual((torin.objectives[0].have, torin.objectives[0].need), (43, 50))
        keeper = by["Keeper of the Sky: Crystal Reclamation"]
        self.assertEqual(len(keeper.objectives), 2)             # no stray "x1"


class DefaultFontAndChatTests(unittest.TestCase):
    def test_default_font_tracker_with_name_tags_over_it(self):
        f = ROOT / "dev_tests" / "fixtures" / "quest_tracker_default_font.png"
        if not f.exists():
            self.skipTest("local tracker fixture not present")
        import cv2
        crop = cv2.imread(str(f))[:, :, ::-1]
        canvas = np.zeros((crop.shape[0], 1920, 3), np.uint8)
        canvas[:, :crop.shape[1]] = crop
        qs = fq.read_tracker(canvas)
        self.assertEqual(len(qs), 8)
        by = {q.title: q for q in qs}
        # green "done" lines over a light sky (they read as "noise" before)
        self.assertEqual([o.done for o in by["Aeronaut Vance: An Air-Worthy Vessel"].objectives],
                         [True, True, False, False])
        self.assertTrue(by["Aero: Swift Winds"].done)
        # one line OCR'd in two pieces, the dark "Rotting" unread: joined, and
        # the mutation comes from the wiki quest
        spirit = by["Everturn Forest: Spirit Seeker"].objectives
        self.assertEqual(len(spirit), 1)
        self.assertEqual(spirit[0].mutations, ["Rotting"])
        self.assertEqual((spirit[0].have, spirit[0].need), (0, 1))

    def test_open_chat_found_closed_chat_not(self):
        import cv2
        open_icon = cv2.imread(str(ROOT / "ui" / "icons" / "roblox" / "chat_open.png"))[:, :, ::-1]
        closed_icon = cv2.imread(str(ROOT / "ui" / "icons" / "roblox" / "chat_closed.png"))[:, :, ::-1]
        frame = np.full((1080, 1920, 3), 28, np.uint8)
        frame[15:15 + open_icon.shape[0], 120:120 + open_icon.shape[1]] = open_icon
        x, y = fq.find_open_chat(frame)
        self.assertTrue(130 <= x <= 155 and 20 <= y <= 50, (x, y))
        frame[15:15 + open_icon.shape[0], 120:120 + open_icon.shape[1]] = 28
        frame[15:15 + closed_icon.shape[0], 120:120 + closed_icon.shape[1]] = closed_icon
        self.assertIsNone(fq.find_open_chat(frame))
        # the closed outline must lose to itself even if the open bubble
        # half-matches it: no click (a false "open" click OPENS the chat)
        from unittest.mock import patch
        with patch.object(fq, "CHAT_MATCH_MIN", 0.6):
            self.assertIsNone(fq.find_open_chat(frame))
        # bigger UI (Roblox scales its topbar with display scaling)
        big = cv2.resize(open_icon, None, fx=1.4, fy=1.4)
        frame2 = np.full((1080, 1920, 3), 28, np.uint8)
        frame2[10:10 + big.shape[0], 160:160 + big.shape[1]] = big
        self.assertIsNotNone(fq.find_open_chat(frame2))


class BotQuestLogTests(unittest.TestCase):
    def test_logs_plan_then_progress_then_completion(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from fischbot import FischBot

        lines, img = _synthetic()
        first = fq.parse_tracker(lines, img)
        later = fq.parse_tracker(lines, img)
        later[0].objectives[1].have, later[0].objectives[1].done = 1, True
        b = FischBot.__new__(FischBot)
        logs = []
        b.log = logs.append
        b.grabber = SimpleNamespace(grab=lambda: np.zeros((1080, 1920, 3), np.uint8))
        b.track_quests, b.owned_rods, b.quests, b._quest_state = True, [], [], None
        with patch("fischquest.read_tracker", side_effect=[first, later]):
            b._check_quests()
            b._check_quests()
        text = "\n".join(logs)
        self.assertIn("quests: 2 tracked", text)
        self.assertIn("plan:", text)
        self.assertIn("Catch and return 1 Gusty Abaia 0/1 -> 1/1 -- DONE", text)
        self.assertIn("QUEST COMPLETE: Aeronaut Vance: An Air-Worthy Vessel -- hand it in "
                      "to Aeronaut Vance (Skycrest)", text)


class RequirementTests(unittest.TestCase):
    """One mutation per fishable; item names may contain mutation words."""

    def test_mutation_word_inside_an_item_name(self):
        import fischnames as fn
        r = fn.requirement("Obtain 1 Gusty Empyrean Relic for Vance")
        self.assertEqual((r["items"], r["mutation"]), (["Empyrean Relic"], "Gusty"))
        self.assertEqual(fn.split_catch("Big Gusty Empyrean Relic"),
                         (("Big",), "Gusty", "Empyrean Relic"))
        self.assertEqual(fn.split_catch("Empyrean Relic"), ((), None, "Empyrean Relic"))
        # attributes don't count toward the one-mutation limit
        self.assertEqual(fn.split_catch("Shiny Sparkling Giant Lunar Abaia"),
                         (("Shiny", "Sparkling", "Giant"), "Lunar", "Abaia"))
        r = fn.requirement("Catch and return 1 Rotting Glaciaseer Sturgeon, Floraseer "
                           "Sturgeon, Solarseer Sturgeon, or Umbraleaf Sturgeon")
        self.assertEqual((len(r["items"]), r["mutation"]), (4, "Rotting"))
        self.assertEqual(fn.requirement("Catch 3 Gusty fish of any kind")["mutation"], "Gusty")
        self.assertIsNone(fn.requirement("Obtain 1 Crested Relic for Vance")["mutation"])

    def test_tracker_objective_gets_one_mutation_and_the_item(self):
        for title in ("Aeronaut Vance: An Air-Worthy Vessel", "Some Quest The Wiki Lacks"):
            q = fq.Quest(title=title, objectives=[
                fq.Objective(text="Obtain 1 Gusty Empyrean Relic for Vance", have=0, need=1)])
            fq._enrich(q)
            o = q.objectives[0]
            self.assertEqual((o.items, o.mutations), (["Empyrean Relic"], ["Gusty"]), title)
            self.assertIn("needs Gusty on Empyrean Relic", fq.plan([q], [])[0])

    def test_catch_line_keeps_the_relic_name(self):
        import fischcatch
        got = fischcatch.parse_caught("You just caught a Gusty Empyrean Relic at 90kg!")
        self.assertEqual((got["fish"], got["mutation"]), ("Empyrean Relic", "Gusty"))


class WikiIndexTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "ui" / name).read_text(encoding="utf-8"))

    def test_weather_index_with_icons_and_totems(self):
        w = {o["name"]: o for o in self.load("weather.json")["weather"]}
        self.assertEqual(w["Aurora Borealis"]["totem"], "Aurora Totem")
        self.assertEqual(w["Lunar Eclipse"]["mutations"].get("Lunar"), 5.0)
        self.assertEqual(w["Mutation Surge"]["group"], "modifier")
        for n in ("Foggy", "Night", "Spring", "Sovereign Storm", "Day/Night of the Luminous"):
            self.assertTrue((ROOT / "ui" / w[n]["icon"]).exists(), n)

    def test_fish_index_has_fish_and_other_fishables(self):
        f = {o["name"]: o for o in self.load("fish.json")["fish"]}
        self.assertGreater(len(f), 1000)
        self.assertTrue(f["Empyrean Relic"]["nonfish"])
        self.assertFalse(f["Abaia"]["nonfish"])
        self.assertEqual(f["Abaia"]["locations"], ["Skycrest"])

    def test_totem_index(self):
        t = {o["name"]: o for o in self.load("totems.json")["totems"]}
        self.assertEqual(t["Mutation Totem"]["weather"], "Mutation Surge")
        self.assertEqual(t["Tempest Totem"]["weather"], "Rain")
        self.assertTrue(t["Sundial Totem"]["cooldown"])
        self.assertFalse(t["Aurora Totem"]["cooldown"])

    def test_quest_giver_index(self):
        npcs = {n["npc"]: n for n in self.load("quests.json")["npcs"]}
        self.assertTrue(npcs["Angler"]["repeatable"])
        self.assertEqual(len(npcs["Angler"]["locations"]), 11)
        vance = npcs["Aeronaut Vance"]["quests"][0]
        tasks = [t for s in vance["steps"] for t in s["tasks"]]
        self.assertIn({"Abaia"}, [set(t["items"]) for t in tasks])
        self.assertTrue(any(t["mutations"] == ["Gusty"] for t in tasks))


if __name__ == "__main__":
    unittest.main()
