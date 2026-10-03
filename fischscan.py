"""
Read the player's rods and enchants from the in-game rod screen (Equipment Bag ->
Fishing Rods), using Windows OCR (fischocr.py).

    cards = parse_rod_screen(rgb)   # one screenful
    # -> [{"rod": "Fabulous Rod", "enchants": ["Starforged Spirit", "Crested",
    #      "Paradise"], "equipped": True, "box": (x0, y0, x1, y1)}, ...]

Card layout, from the user's screenshot (2026-10-02):

    Lure Speed / Luck / Control / Resilience / Max KG / Progress Speed /
    Disturbance / [Hunt Focus] / Caught            <- stat lines (top)
    <rod art>
    ◆ Primary Enchant ◆                          <- enchant lines
    Secondary, Enchants
    [Power bar]
    [Rod Name]                                     <- anchor: brackets + known rod
    [Skin Name] / flavour text
    [Equip] / [Equipped]

OCR misreads a letter or two ("[Fabulous 20dJ", "Greste Paradise"), so names are
fuzzy-matched against the wiki lists, and enchants are only looked for between
the stat block and the rod name -- matching every line made "Control:" read as
the enchant "Controlled".
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

from fischocr import _norm, best_match, ocr_lines
from fischrods import ENCHANTS, rod_names

STAT_WORDS = ("lure", "luck", "control", "resil", "kg", "progress", "disturb",
              "caught", "hunt", "focus", "speed")
ROD_CUTOFF = 0.75
# Looser than ROD_CUTOFF on purpose: enchants are only searched in the few lines
# between the stat block and the rod name. "Greste" -> "Crested" scores 0.77.
ENCH_CUTOFF = 0.72


def _is_stat(text: str) -> bool:
    t = text.lower()
    return ":" in t and any(w in t for w in STAT_WORDS)


def _enchants_in(text: str) -> list[str]:
    """Enchant names in one OCR line, trying 3-, 2- then 1-word windows."""
    words = re.findall(r"[A-Za-z']+", text)
    found, i = [], 0
    while i < len(words):
        hit = None
        for n in (3, 2, 1):
            if i + n > len(words):
                continue
            chunk = " ".join(words[i:i + n])
            if n == 1 and len(chunk) < 4:
                continue
            m = best_match(chunk, ENCHANTS, ENCH_CUTOFF)
            # a 1-word window may only match a 1-word enchant
            if m and (n > 1 or " " not in m):
                hit = (m, n)
                break
        if hit:
            if hit[0] not in found:
                found.append(hit[0])
            i += hit[1]
        else:
            i += 1
    return found


def parse_rod_screen(rgb: np.ndarray, lines=None) -> list[dict]:
    lines = lines if lines is not None else ocr_lines(rgb, scale=2.0)
    rods_known = rod_names()

    anchors = []
    for text, box in lines:
        if "[" not in text and "(" not in text:
            continue
        inner = re.sub(r"^[^A-Za-z]*|[^A-Za-z']*$", "", text)
        rod = best_match(inner, rods_known, ROD_CUTOFF)
        if rod:
            anchors.append((rod, box))
    if not anchors:
        return []
    anchors.sort(key=lambda a: (a[1][0] + a[1][2]) / 2)
    centres = [(b[0] + b[2]) / 2 for _, b in anchors]
    # A grid may have several names in the same column. Zero/tiny gaps are
    # not card widths, particularly when the bag reflows in a narrow window.
    gaps = np.diff(centres)
    gaps = gaps[gaps > max(8, np.median([b[3] - b[1] for _, b in anchors]) * 2)]
    card_w = float(np.median(gaps)) if len(gaps) else rgb.shape[1] * 0.3

    cards = []
    for (rod, rbox), cx in zip(anchors, centres):
        x0, x1 = cx - card_w * 0.48, cx + card_w * 0.48
        mine = [(t, b) for t, b in lines if x0 <= (b[0] + b[2]) / 2 <= x1 or
                (b[0] >= x0 - 10 and b[0] < cx and _is_stat(t))]
        stat_bottom = max((b[3] for t, b in mine if _is_stat(t) and b[3] < rbox[1]),
                          default=None)
        enchants = []
        for t, b in sorted(mine, key=lambda l: l[1][1]):
            if b[3] > rbox[1] or (stat_bottom is not None and b[1] <= stat_bottom):
                continue
            if _is_stat(t) or "power" in t.lower():
                continue
            for e in _enchants_in(t):
                if e not in enchants:
                    enchants.append(e)
        bottom = min((b[1] for _, b in anchors if b[1] > rbox[3]
                      and abs((b[0] + b[2]) / 2 - cx) < card_w * .48), default=rgb.shape[0])
        equipped = any("equipped" in _norm(t) and rbox[3] < b[1] < bottom for t, b in mine)
        cards.append({"rod": rod, "enchants": enchants, "equipped": equipped,
                      "box": (int(x0), 0, int(x1), int(rbox[3])),
                      "name_box": rbox,
                      "bottom": bottom})
    return cards


def merge_scan(found: dict[str, dict], cards: list[dict]) -> int:
    """Add one screen's cards to the running result; returns how many were new."""
    new = 0
    for c in cards:
        prev = found.get(c["rod"])
        if prev is None:
            found[c["rod"]] = {"enchants": c["enchants"], "equipped": c["equipped"]}
            new += 1
        else:
            # keep the reading with more enchants (a card half off-screen reads fewer)
            if len(c["enchants"]) > len(prev["enchants"]):
                prev["enchants"] = c["enchants"]
            prev["equipped"] = prev["equipped"] or c["equipped"]
    return new
