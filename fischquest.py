"""
Read the quest tracker (left side of the screen) and plan how to finish quests.

    quests = read_tracker(frame)        # RGB client frame
    for q in quests: print(q.title, [(o.text, o.done, o.have, o.need) for o in q.objectives])
    for line in plan(quests, owned_rods): print(line)

What the tracker looks like (the user's screenshot, 2026-10-03, 1920-wide
client; dev_tests/fixtures/quest_tracker_custom_font.png):

    Aeronaut Vance: An Air-Worthy Vessel          <- title: pure white (~252), underlined
    ✓ Catch 1 African Butterflyfish (1/1)        <- objective done: green (146,211,150)
      Catch and return 1 Gusty Abaia (0/1)       <- objective: off-white (~230), indented
    Within Poseidon's storm, the void grows ...  <- description: grey (~190), least indented
      Catch and return 1 Rotting Glaciaseer Sturgeon, Floraseer Sturgeon,
      Solarseer Sturgeon, or Umbraleaf Sturgeon (0/1)   <- wrapped: closer line spacing

Lines are told apart by colour first and indent second; wrapped lines are
joined by their tighter spacing. Requirements (fish, required mutation,
perfect catch) come from the wiki quest index (ui/quests.json, fischwiki.py)
matched by title -- the OCR'd text is only used for progress and as a fallback.
Nothing is saved: only quest text is ever logged (never chat or other text
the OCR may pass over on the left of the screen).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import numpy as np

from fischnames import ATTRIBUTES, requirement

UI = Path(__file__).with_name("ui")
TRACKER_W_FRAC = 0.34           # the tracker spans the left third of the client
PROGRESS_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)\s*\.?\s*$")
NOISE_RE = re.compile(r"^(uptime|location|version|ping|fps)\b|^[\d.o]+\s*-\s*\d+$|xp$", re.I)


@dataclass
class Objective:
    text: str
    done: bool = False
    have: Optional[int] = None
    need: Optional[int] = None
    # from the wiki quest index when the quest is known, else parsed from text
    items: list[str] = field(default_factory=list)
    # at most one: a fishable carries one mutation (attributes don't count)
    mutations: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)   # Shiny, Big ...
    perfect: bool = False

    @property
    def remaining(self) -> Optional[int]:
        if self.done:
            return 0
        if self.have is not None and self.need is not None:
            return max(0, self.need - self.have)
        return None


@dataclass
class Quest:
    title: str
    description: str = ""
    objectives: list[Objective] = field(default_factory=list)
    npc: Optional[str] = None           # quest giver, from the wiki index
    location: Optional[str] = None

    @property
    def done(self) -> bool:
        return bool(self.objectives) and all(o.done for o in self.objectives)


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------

_cache: dict = {}


def _load(name: str) -> dict:
    if name not in _cache:
        try:
            _cache[name] = json.loads((UI / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[name] = {}
    return _cache[name]


def quest_index() -> dict[str, tuple[dict, dict]]:
    """Quest name -> (quest, its NPC) from ui/quests.json."""
    if "_qi" not in _cache:
        out = {}
        for npc in _load("quests.json").get("npcs", []):
            for q in npc.get("quests", []):
                if q.get("name"):
                    out[q["name"]] = (q, npc)
        _cache["_qi"] = out
    return _cache["_qi"]


def mutation_index() -> dict[str, dict]:
    return {m["name"]: m for m in _load("mutations.json").get("mutations", [])}


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


def _line_kind(img: np.ndarray, box) -> tuple[str, float]:
    """("title" | "objective" | "done" | "description" | "noise", brightness)."""
    x0, y0, x1, y1 = box
    px = img[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3).astype(int)
    px = px[px.max(1) > 150]
    if len(px) < 8:
        return "noise", 0.0              # translucent overlay text (server info)
    # Done objectives are light green (146,211,150). Over a light background
    # (sky, sand) part of the box is background, so judge the text pixels'
    # median hue, not the share of strongly green pixels (live, default font:
    # 0.26-0.54 green share on finished objectives).
    green = ((px[:, 1] > px[:, 0] + 40) & (px[:, 1] > px[:, 2] + 40)).mean()
    med = np.median(px, axis=0)
    level = float(np.median(px.max(1)))
    if green > 0.5 or (green > 0.2 and med[1] > med[0] + 20 and med[1] > med[2] + 20):
        return "done", level
    sat = float(np.median(px.max(1) - px.min(1)))
    if sat > 40:
        return "noise", level            # coloured: fish name tags etc.
    if level >= 245:
        return "title", level
    if level >= 212:
        return "objective", level
    if level >= 165:
        return "description", level
    return "noise", level


def _merge_rows(lines, h: float):
    """OCR sometimes splits one tracker line in two (default font, live:
    "Catch and return 1" | "Glaciaseer Sturgeon, ..." -- the dark "Rotting"
    between them unread). Join pieces on the same row, left to right."""
    out: list[list] = []
    for text, box in sorted(lines, key=lambda l: (l[1][1], l[1][0])):
        for row in out:
            rb = row[1]
            same = abs((box[1] + box[3]) / 2 - (rb[1] + rb[3]) / 2) <= 0.4 * h
            if same and 0 <= box[0] - rb[2] <= 14 * h and abs(box[3] - box[1] - (rb[3] - rb[1])) < 0.5 * h:
                row[0] = row[0] + " " + text
                row[1] = (rb[0], min(rb[1], box[1]), box[2], max(rb[3], box[3]))
                break
        else:
            out.append([text, box])
    return [(t, b) for t, b in out]


def parse_tracker(lines, img: np.ndarray) -> list[Quest]:
    """Quests from OCR lines [(text, (x0,y0,x1,y1))] of the tracker image."""
    if not lines:
        return []
    hs = [b[3] - b[1] for _, b in lines]
    h = float(np.median(hs))
    lines = _merge_rows(lines, h)
    rows = []
    for text, box in sorted(lines, key=lambda l: l[1][1]):
        t = text.strip()
        # too short to be quest text: the green "x1" of the XP-boost icon
        # below the tracker read as a finished objective
        if len(t) < 4 or NOISE_RE.search(t) or box[3] - box[1] > 1.6 * h:
            continue
        kind, _ = _line_kind(img, box)
        if kind == "noise":
            continue
        # The tracker is one block: once it has started, a big gap ends it.
        # (Not before: an open chat sits above the quests with a gap.)
        if any(r[0] == "title" for r in rows) and box[1] - rows[-1][2][3] > 6 * h:
            break
        rows.append([kind, t, box])
    # The objective column: lines with a progress counter sit there.
    obj_x = [b[0] for k, t, b in rows if k in ("objective", "done") and PROGRESS_RE.search(t)]
    col = float(np.median(obj_x)) if obj_x else None
    if col is not None:
        # Quest text starts at one of three indents (description < title <
        # objective). Fish name tags floating over the tracker do not.
        rows = [r for r in rows if r[2][0] <= col + 0.6 * h]
        for r in rows:
            # A white line indented like an objective is an objective; a grey
            # line well left of the column is a description.
            if r[0] == "title" and abs(r[2][0] - col) <= 0.3 * h:
                r[0] = "objective"
            elif r[0] == "objective" and r[2][0] < col - 1.2 * h:
                r[0] = "description"

    quests: list[Quest] = []
    prev = None
    for kind, text, box in rows:
        tight = prev is not None and box[1] - prev[2][3] < 0.45 * h
        if kind == "title":
            quests.append(Quest(title=text))
        elif not quests:
            pass                                 # above the first title: not ours
        elif kind == "description":
            q = quests[-1]
            q.description = (q.description + " " + text).strip() if tight or not q.description \
                else q.description + " " + text
        else:
            q = quests[-1]
            last = q.objectives[-1] if q.objectives else None
            if (tight and last is not None and prev[0] in ("objective", "done")
                    and not PROGRESS_RE.search(last.text)):
                last.text += " " + text          # a wrapped objective
                last.done = last.done or kind == "done"
            else:
                q.objectives.append(Objective(text=text, done=kind == "done"))
        prev = (kind, text, box)
    for q in quests:
        for o in q.objectives:
            m = PROGRESS_RE.search(o.text)
            if m:
                o.have, o.need = int(m.group(1)), int(m.group(2))
                o.text = o.text[:m.start()].strip()
                o.done = o.done or o.have >= o.need
        _enrich(q)
    return quests


def _enrich(q: Quest) -> None:
    """Requirements from the wiki quest (matched by title), else from text."""
    idx = quest_index()
    hit = idx.get(q.title)
    if hit is None and idx:
        best = max(idx, key=lambda n: SequenceMatcher(None, n.lower(), q.title.lower()).ratio())
        if SequenceMatcher(None, best.lower(), q.title.lower()).ratio() >= 0.85:
            hit = idx[best]
            q.title = best                      # OCR slips: "Evertum" -> "Everturn"
    tasks = []
    if hit:
        quest, npc = hit
        q.npc = npc.get("npc")
        q.location = (npc.get("locations") or [None])[0]
        tasks = [t for s in quest.get("steps", []) for t in s.get("tasks", []) if t.get("text")]
    for o in q.objectives:
        task = None
        if tasks:
            task = max(tasks, key=lambda t: SequenceMatcher(None, t["text"].lower(),
                                                            o.text.lower()).ratio())
            if SequenceMatcher(None, task["text"].lower(), o.text.lower()).ratio() < 0.6:
                task = None
        # The tracker's own words, split with the fish index: "Gusty Empyrean
        # Relic" is the item Empyrean Relic with the mutation Gusty (Empyrean
        # is a mutation too, but here it is part of the item's name).
        req = requirement(o.text)
        o.perfect = "perfect catch" in o.text.lower()
        o.items, o.attributes = req["items"], req["attributes"]
        o.mutations = [req["mutation"]] if req["mutation"] else []
        if task:
            # The wiki's {{Fish|X|attrs=...}} is cleaner than OCR; the game
            # text still wins where the wiki has nothing (pages lag updates).
            o.items = task["items"] or o.items
            o.perfect = o.perfect or task["perfect"]
            wiki_attrs = [a for a in task["mutations"] if a in ATTRIBUTES]
            wiki_muts = [a for a in task["mutations"] if a not in ATTRIBUTES]
            o.attributes = list(dict.fromkeys(wiki_attrs + o.attributes))
            o.mutations = wiki_muts[:1] or o.mutations


def read_tracker(frame: np.ndarray, ocr=None) -> list[Quest]:
    """Quests on the tracker in an RGB client frame (left third, OCR'd)."""
    if ocr is None:
        from fischocr import ocr_lines as ocr
    w = frame.shape[1]
    region = np.ascontiguousarray(frame[:, :int(w * TRACKER_W_FRAC)])
    scale = float(np.clip(1300 / max(1, region.shape[1]), 1.5, 2.5))
    return parse_tracker(ocr(region, scale=scale), region)


# --------------------------------------------------------------------------------------
# Roblox chat: an open chat window can cover the tracker
# --------------------------------------------------------------------------------------
#
# The topbar's chat button (top-left) is a white OUTLINE bubble while chat is
# closed (often with a blue unread badge) and a SOLID white bubble while it is
# open (the user's crops, ui/icons/roblox/chat_closed.png / chat_open.png). So
# "is chat open" = is the solid bubble there; the bot clicks it to close.

CHAT_MATCH_MIN = 0.8
_chat_tpl = None


def _chat_template() -> Optional[np.ndarray]:
    global _chat_tpl
    if _chat_tpl is None:
        import cv2
        im = cv2.imread(str(UI / "icons" / "roblox" / "chat_open.png"), cv2.IMREAD_UNCHANGED)
        if im is None:
            return None
        g = cv2.cvtColor(im[..., :3], cv2.COLOR_BGR2GRAY)
        ys, xs = np.nonzero(g > 200)
        pad = 3                                  # some dark margin around the bubble
        _chat_tpl = g[max(0, ys.min() - pad):ys.max() + pad + 1,
                      max(0, xs.min() - pad):xs.max() + pad + 1]
    return _chat_tpl


def find_open_chat(frame: np.ndarray) -> Optional[tuple[int, int]]:
    """Centre (client px) of the chat button if chat is OPEN, else None."""
    import cv2
    tpl = _chat_template()
    if tpl is None:
        return None
    h, w = frame.shape[:2]
    region = cv2.cvtColor(np.ascontiguousarray(frame[:min(h, 120), :min(w, 460)]),
                          cv2.COLOR_RGB2GRAY)
    best = (0.0, None)
    for s in np.arange(0.6, 1.65, 0.1):
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= region.shape[0] or t.shape[1] >= region.shape[1]:
            continue
        r = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _, v, _, loc = cv2.minMaxLoc(r)
        if v > best[0]:
            best = (v, (loc[0] + t.shape[1] // 2, loc[1] + t.shape[0] // 2))
    return best[1] if best[0] >= CHAT_MATCH_MIN else None


def quests_view(quests: list[Quest], owned_rods: list[str]) -> dict:
    """Plain data for the UI's Quests tab."""
    return {"quests": [{
        "title": q.title, "npc": q.npc, "location": q.location, "done": q.done,
        "description": q.description,
        "objectives": [{"text": o.text, "done": o.done, "have": o.have, "need": o.need,
                        "items": o.items, "mutations": o.mutations,
                        "attributes": o.attributes, "perfect": o.perfect}
                       for o in q.objectives]} for q in quests],
        "plan": plan(quests, owned_rods)}


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


def plan(quests: list[Quest], owned_rods: list[str]) -> list[str]:
    """Plain-language suggestions for unfinished objectives that need a
    mutation: an owned rod that gives it (best chance first), else the
    weathers / totems / enchants that do."""
    muts = mutation_index()
    weather = _load("weather.json").get("weather", [])
    out = []
    for q in quests:
        for o in q.objectives:
            if o.done or not o.mutations:
                continue
            for name in o.mutations:
                m = muts.get(name)
                need = f"{q.title}: {o.text}" + (f" ({o.have}/{o.need})" if o.need else "")
                if not m:
                    out.append(f"{need} -- needs {name} (not in the mutation index)")
                    continue
                rods = m.get("rods") or {}
                mine = sorted(((r, c) for r, c in rods.items() if r in owned_rods),
                              key=lambda rc: -(rc[1] or 0))
                ways = []
                if mine:
                    ways.append("use " + ", ".join(f"{r}" + (f" ({c:g}%)" if c else "")
                                                   for r, c in mine))
                else:
                    if rods:
                        ways.append("rods you don't own: " + ", ".join(list(rods)[:4]))
                    ws = [w for w in weather if name in (w.get("mutations") or {})]
                    for w in ws[:3]:
                        c = w["mutations"][name]
                        ways.append(f"weather {w['name']}" + (f" ({c:g}%)" if c else "")
                                    + (f" -- {w['totem']}" if w.get("totem") else ""))
                    for e in (m.get("enchants") or [])[:2]:
                        ways.append(f"enchant {e}")
                    if not ways and m.get("group") == "natural":
                        ways.append("natural chance only")
                    if m.get("group") == "event":
                        ways.append("event/limited mutation")
                on = f" on {' or '.join(o.items[:3])}" if o.items else ""
                out.append(f"{need} -- needs {name}{on}" + (" (perfect catch)" if o.perfect else "")
                           + ": " + ("; ".join(ways) if ways else "no known source"))
    return out
