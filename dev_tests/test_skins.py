"""Reel-bar skins: a shorter skin track, both-bright progress, skin memory.

From the user's pastel Fabulous Rod skin (2026-10-03): track x 601-1324 under
a bright art banner (saved_logs/20261003_174120 read the banner instead: rows
809-839, "slider 364-469px", no progress verdict), a pink-white-yellow fill
over a muted pink empty part, and the fish's bright line just outside the
slider."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischtrack as ft  # noqa: E402
import fischskins as fs  # noqa: E402

FIX = ROOT / "dev_tests" / "fixtures"


def _pastel():
    import cv2
    im = cv2.imread(str(FIX / "bar_pastel_skin_full.png"))[:, :, ::-1]
    f = np.zeros((1009, 1920, 3), np.uint8)
    f[790:790 + im.shape[0], 574:574 + im.shape[1]] = im   # track x 601-1324, y 846-878
    return f


def _crop(name, ya):
    import cv2
    im = cv2.imread(str(FIX / name))[:, :, ::-1]
    f = np.zeros((1009, 1920, 3), np.uint8)
    f[ya:ya + im.shape[0], 511:511 + im.shape[1]] = im
    return f


class SkinReadingTests(unittest.TestCase):
    def setUp(self):
        ft.set_client(1920, 1009)
        ft.set_skin_track_k(None)
        if not (FIX / "bar_pastel_skin_full.png").exists():
            self.skipTest("local skin screenshot not present")

    def test_short_track_under_skin_art(self):
        r = ft.read_track(_pastel())
        self.assertIsNotNone(r)
        self.assertEqual((r.y0, r.y1), (846, 876))
        self.assertTrue(580 <= r.track_x0 <= 605 and 1320 <= r.track_x1 <= 1340, r)
        # slider 666-926 (the fish's line at 961 just outside is not slider)
        self.assertTrue(abs(r.slider_x0 - 666) <= 6 and abs(r.slider_x1 - 926) <= 8, r)
        self.assertTrue(abs(r.marker_x - 961) <= 4, r)

    def test_both_bright_progress_reads_by_its_fill_step(self):
        f = _pastel()
        pol = ft.ProgressPolarity()
        got = [ft.find_progress(f, 909, 932, scale=1.0, polarity=pol, expect_top=915)
               for _ in range(4)]
        self.assertIsNone(got[0])                      # undecided at first ...
        self.assertTrue(pol.step)                      # ... then step mode
        self.assertAlmostEqual(got[-1][0], 0.68, delta=0.02)

    def test_default_skins_unchanged(self):
        if not (FIX / "bar_fabulous_plain.png").exists():
            self.skipTest("local bar crops not present")
        r = ft.read_track(_crop("bar_fabulous_plain.png", 796))
        self.assertEqual((r.track_x0, r.track_x1, r.y0), (571, 1348, 846))

    def test_fingerprints_tell_skins_apart(self):
        a = fs.fingerprint(_pastel(), ft.read_track(_pastel()))
        self.assertAlmostEqual(a["k"], 0.955, delta=0.02)
        if (FIX / "bar_fabulous_plain.png").exists():
            f = _crop("bar_fabulous_plain.png", 796)
            b = fs.fingerprint(f, ft.read_track(f))
            self.assertEqual(b["k"], 1.0)
            self.assertFalse(fs.same_skin(a, dict(b, id="x")))
        self.assertTrue(fs.same_skin(a, dict(a, id="x")))


class SkinBookTests(unittest.TestCase):
    def tearDown(self):
        ft.set_skin_track_k(None)

    def test_learn_recognise_and_prime(self):
        logs, saved = [], []
        book = fs.SkinBook([], logs.append, save=lambda sk: saved.append([dict(s) for s in sk]))
        fp = {"k": 0.955, "track": [20, 14, 16], "slider": [250, 200, 210], "method": "geo"}
        book.begin(fp, "Fabulous Rod")
        book.end("Fabulous Rod", ["Crested"], readable=False, caught=False, perfect=None,
                 progress=None, slider_w=None)
        self.assertEqual(book.skins, [])               # unreadable reel: not saved
        book.begin(fp, "Fabulous Rod")
        book.end("Fabulous Rod", ["Crested"], readable=True, caught=True, perfect=True,
                 progress="step", slider_w=258)
        self.assertEqual(book.skins[0]["name"], "Skin 1")
        self.assertEqual(saved[-1][0]["progress"], "step")
        near = dict(fp, track=[24, 16, 18], slider=[240, 196, 214])
        self.assertIs(book.begin(near, "Fabulous Rod"), book.skins[0])
        self.assertEqual(sum("not one seen before" in l for l in logs), 1)   # logged once
        # a new run with this rod: track search primed with the skin's length
        book2 = fs.SkinBook(saved[-1], logs.append)
        self.assertEqual(book2.prime("Fabulous Rod", ["Crested"]), 258)
        self.assertEqual(ft.skin_track_k(), 0.955)
        self.assertIs(book2.begin(near, "Fabulous Rod"), book2.skins[0])
        self.assertIn("'Skin 1' recognised", " | ".join(logs))
        self.assertIsNone(book2.prime("Duskwire", []))
        self.assertIsNone(ft.skin_track_k())


if __name__ == "__main__":
    unittest.main()


class SkinApiTests(unittest.TestCase):
    def test_rename_and_forget_in_the_general_config(self):
        import json
        import tempfile
        from unittest.mock import patch
        from fischuse import load_general, save_general
        import fischui
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as d:
            g = Path(d) / "general.json"
            save_general({"skins": [{"id": "s1", "name": "Skin 1", "k": 0.955, "track": [20, 14, 16],
                                     "slider": [250, 200, 210], "rods": {}}]}, g)
            with patch("fischui.load_general", lambda: load_general(g)), \
                    patch("fischui.save_general", lambda data: save_general(data, g)), \
                    patch("fischui.PROFILES_FILE", Path(d) / "p.json"):
                api = fischui.Api()
                self.assertEqual(api.edit_skin("s1", "Pastel")["skins"][0]["name"], "Pastel")
                self.assertEqual(json.loads(g.read_text())["skins"][0]["name"], "Pastel")
                self.assertFalse(api.edit_skin("s1", "  ")["ok"])
                self.assertEqual(api.edit_skin("s1", forget=True)["skins"], [])
                self.assertNotIn("skins", fischui.DEFAULTS)      # not part of saved setups
