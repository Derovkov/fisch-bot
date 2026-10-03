"""Mutation index (ui/mutations.json from fischwiki.py) and catch-line parsing."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischcatch import catch_notice, parse_caught  # noqa: E402


class MutationIndexTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "ui" / "mutations.json").read_text(encoding="utf-8"))
        self.by = {m["name"]: m for m in self.data["mutations"]}

    def test_index_has_every_group_and_known_rod_sources(self):
        groups = {m["group"] for m in self.data["mutations"]}
        self.assertEqual(groups, {"rod", "enchant", "natural", "event"})
        self.assertGreater(len(self.data["mutations"]), 300)
        # the user's catch "Chaotic Pike" with Duskwire; quest mutations
        self.assertIn("Duskwire", self.by["Chaotic"]["rods"])
        self.assertIn("Moonlit Rod", self.by["Lunar"]["rods"])
        self.assertIn("Lucid Rod", self.by["Lucid"]["rods"])
        self.assertEqual(self.by["Fabulous"]["rods"], {"Fabulous Rod": 49.0})

    def test_groups_follow_sources(self):
        for m in self.data["mutations"]:
            if m["group"] == "rod":
                self.assertTrue(m.get("rods") or "Fishing Rods" in m["sources"], m["name"])
            if m["group"] == "enchant":
                self.assertFalse(m.get("rods"), m["name"])


class CatchLineTests(unittest.TestCase):
    def test_mutation_attributes_fish_and_weight(self):
        self.assertEqual(parse_caught("You just caught a Chaotic Pike at 1.2kg! (12.97%)"),
                         {"fish": "Pike", "mutation": "Chaotic", "attributes": (), "kg": 1.2})
        p = parse_caught("You just caught a Glitched Shiny Big Silver Isonade at 3,402.5kg!")
        self.assertEqual((p["fish"], p["mutation"], p["attributes"], p["kg"]),
                         ("Isonade", "Silver", ("Glitched", "Shiny", "Big"), 3402.5))
        self.assertEqual(parse_caught("You just caught a Hades' Curse Great White Shark at 900kg")
                         ["mutation"], "Hades' Curse")
        self.assertIsNone(parse_caught("You just caught a Pike at 0.8kg!")["mutation"])
        self.assertEqual(parse_caught("You just caught a Chaotlc Pike at 1.2kg")["mutation"], "Chaotic")

    def test_game_font_misreads_and_split_rows(self):
        # recording frame 975: "kg" read "k9", the line split in two OCR lines
        n = catch_notice([("Sockeyeemon at 13.4k9! (D.DI%)", (260, 10, 420, 22)),
                          ("You just caught a Big", (10, 10, 250, 22))], "Fabulous Rod")
        self.assertEqual((n.source, n.attributes, n.kg), ("caught message", ("Big",), 13.4))
        # Duskwire's passive confirms, and the catch line is still read
        n = catch_notice([("Chaotic energy surges through your strings...", (10, 40, 300, 52)),
                          ("You just caught a Chaotic Pike at 1.2kg!", (10, 10, 300, 22))], "Duskwire")
        self.assertEqual((n.source, n.fish, n.mutation), ("duskwire passive", "Pike", "Chaotic"))


if __name__ == "__main__":
    unittest.main()
