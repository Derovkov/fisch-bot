"""
Useables: totems (used from the hotbar) and baits (equipped in the Equipment
Bag), each with limits -- the Useables tab.

The user (2026-10-03): a tab for usables and baits, with an option to limit how
many the bot can use, or to stop using one when its amount reaches a number
"until we get more". Kept apart from the hot-swap setups: it lives in the
general config file (fischbot_general.json), not in fischbot_profiles.json.

Per item:
    max_uses   -- at most this many per run (0 = no limit). A bait use is a cast.
    keep       -- stop when the amount left is at or below this; it resumes by
                  itself once the game shows more again.

Totems ("when"):
    start      -- once, at the start of the run
    every      -- every `every_min` minutes
    quest      -- when a tracked quest needs a mutation its weather gives (the
                  Mutation Totem: any mutation), at most every `every_min` minutes

Baits: an ordered list. The bot keeps the first bait that is on and within its
limits equipped; when it runs down to `keep` (or hits `max_uses`) it equips
the next. When none is left: stop the bot, or keep fishing.

What it reads (the user's screenshot, dev_tests/fixtures/hotbar_default_font.png):
    "Current Bait: Cupcakes [x179]" above the power bar -- the bait in use and
    how many are left (the bait name is blue: read on the max colour channel);
    "x92" at the top-right of a hotbar slot -- a stack's amount;
    the held item's slot has a white frame.
Using a totem: press its slot's number (it is now in hand -- checked by the
frame), click, then T takes the rod back (checked too).

Not yet: reading the weather, so a totem whose weather is already on (or is
blocked by the current weather) can still be used -- the gap guards that.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
from typing import Callable, Optional

import numpy as np

GENERAL_FILE = Path(__file__).with_name("fischbot_general.json")
UI = Path(__file__).with_name("ui")
WHEN = ("start", "every", "quest")
DEFAULT_USEABLES = {"enabled": False,
                    "bait": {"manage": False, "when_out": "stop", "list": []},
                    "totems": []}
TOTEM_DEFAULT = {"on": True, "when": "every", "every_min": 15, "max_uses": 0, "keep": 0}
BAIT_DEFAULT = {"on": True, "max_uses": 0, "keep": 0}
RETRY_S = 300                # an item that could not be found/used: try again after
HELD_MIN = 0.55              # share of a slot's side columns that is white frame


# --------------------------------------------------------------------------------------
# General config (separate from hot-swap setups)
# --------------------------------------------------------------------------------------


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        return float(min(hi, max(lo, float(v))))
    except (TypeError, ValueError):
        return default


def clean_useables(raw) -> dict:
    """Validated useables settings (unknown keys dropped, numbers clamped)."""
    raw = raw if isinstance(raw, dict) else {}
    bait = raw.get("bait") if isinstance(raw.get("bait"), dict) else {}
    out = {"enabled": bool(raw.get("enabled", False)),
           "bait": {"manage": bool(bait.get("manage", False)),
                    "when_out": "continue" if bait.get("when_out") == "continue" else "stop",
                    "list": []},
           "totems": []}
    seen = set()
    for b in bait.get("list", []) if isinstance(bait.get("list"), list) else []:
        if isinstance(b, dict) and isinstance(b.get("name"), str) and b["name"] not in seen:
            seen.add(b["name"])
            out["bait"]["list"].append({
                "name": b["name"], "on": bool(b.get("on", True)),
                "max_uses": int(_num(b.get("max_uses"), 0, 1e6, 0)),
                "keep": int(_num(b.get("keep"), 0, 1e6, 0))})
    seen = set()
    for t in raw.get("totems", []) if isinstance(raw.get("totems"), list) else []:
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"] not in seen:
            seen.add(t["name"])
            out["totems"].append({
                "name": t["name"], "on": bool(t.get("on", True)),
                "when": t.get("when") if t.get("when") in WHEN else "every",
                "every_min": _num(t.get("every_min"), 1, 600, 15),
                "max_uses": int(_num(t.get("max_uses"), 0, 1e4, 0)),
                "keep": int(_num(t.get("keep"), 0, 1e6, 0))})
    return out


def load_general(path: Path = GENERAL_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data["useables"] = clean_useables(data.get("useables"))
    return data


def save_general(data: dict, path: Path = GENERAL_FILE) -> dict:
    current = load_general(path)
    current.update({k: v for k, v in data.items() if k != "useables"})
    if "useables" in data:
        current["useables"] = clean_useables(data["useables"])
    path.write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current


def _index(name: str, key: str) -> list[dict]:
    try:
        return json.loads((UI / name).read_text(encoding="utf-8")).get(key, [])
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------------------
# Reading the screen
# --------------------------------------------------------------------------------------


def _count(text: str) -> Optional[int]:
    """The amount in "[x179]" / "x92", OCR-tolerant: the "x" reads as k, >, <;
    a 1 as l / I / |; a 0 as o."""
    t = text.strip().strip("[]()").strip()
    t = re.sub(r"^[x×XkK><*]+", "", t)
    t = t.translate(str.maketrans({"l": "1", "I": "1", "|": "1", "i": "1", "o": "0", "O": "0"}))
    t = re.sub(r"[\s,.]", "", t).rstrip("]")
    return int(t) if re.fullmatch(r"\d{1,7}", t) else None


def parse_bait_line(texts: list[str], bait_names: list[str]) -> tuple[Optional[str], Optional[int]]:
    """(bait, amount) from OCR reads of the "Current Bait: Name [xN]" line,
    voting across reads (each read garbles a different part)."""
    from fischocr import best_match

    names, counts = Counter(), Counter()
    for raw in texts:
        m = re.search(r"ba[il1/t]*[\s.:;i!@]*(.*)$", raw, flags=re.I)
        if not m:
            continue
        rest = m.group(1)
        k = rest.rfind("[")
        if k < 0:
            k = max(rest.rfind(">"), rest.rfind("<"))
        name_part, count_part = (rest[:k], rest[k:]) if k >= 0 else (rest, "")
        c = _count(count_part) if count_part else None
        if c is not None:
            counts[c] += 1
        name = best_match(re.sub(r"[^A-Za-z' -]", "", name_part).strip(), bait_names, 0.7)
        if name:
            names[name] += 1
    return (names.most_common(1)[0][0] if names else None,
            counts.most_common(1)[0][0] if counts else None)


def read_current_bait(frame: np.ndarray, bait_names: Optional[list[str]] = None,
                      ocr=None) -> tuple[Optional[str], Optional[int]]:
    """The bait in use and its amount, from the line above the hotbar."""
    if ocr is None:
        from fischocr import ocr_lines as ocr
    from fischequip import hotbar_band

    if bait_names is None:
        bait_names = [b["name"] for b in _index("baits.json", "baits")]
    band = hotbar_band(frame)
    bright = np.ascontiguousarray(np.repeat(band.max(2, keepdims=True), 3, 2))
    line = next((b for t, b in ocr(bright, scale=2.0) if re.search(r"bait", t, re.I)), None)
    if line is None:
        return None, None
    h = line[3] - line[1]
    x0, x1 = max(0, line[0] - h), min(band.shape[1], line[2] + 12 * h)
    y0, y1 = max(0, line[1] - h // 2), min(band.shape[0], line[3] + h // 2)
    crop = band[y0:y1, x0:x1]
    cmax = np.ascontiguousarray(np.repeat(crop.max(2, keepdims=True), 3, 2))
    texts = [t for img, sc in ((crop, 2.0), (cmax, 3.0), (cmax, 2.0), (crop, 3.0))
             for t, _ in ocr(np.ascontiguousarray(img), scale=sc)]
    return parse_bait_line(texts, bait_names)


def slot_counts(words, centres: np.ndarray, pitch: float) -> dict[int, int]:
    """{slot number: amount} from "x92" words at the top-right of slots."""
    out = {}
    for t, b in words:
        if not re.fullmatch(r"[x×][\dlIO]{1,6}", t.strip()):
            continue
        c = _count(t)
        cx = (b[0] + b[2]) / 2
        k = int(np.argmin(np.abs(centres - cx)))
        if c is not None and abs(centres[k] - cx) <= 0.55 * pitch:
            out[k + 1] = c
    return out


def _longest_runs(mask: np.ndarray) -> np.ndarray:
    """Per column: the longest unbroken vertical run of True."""
    best = np.zeros(mask.shape[1], int)
    run = np.zeros(mask.shape[1], int)
    for row in mask:
        run = np.where(row, run + 1, 0)
        best = np.maximum(best, run)
    return best


def held_slot(band: np.ndarray, centres: np.ndarray, pitch: float, top: int = 0) -> Optional[int]:
    """The slot (1..) with the white frame of the item in hand, or None.

    The frame's two sides are unbroken white columns about a slot tall and a
    slot apart (fixture: x 62 and 129, 67px tall, pitch 69); label text never
    makes runs that long. Found by shape, then given to the nearest slot, so
    a slightly-off slot fit does not matter."""
    runs = _longest_runs(band.min(2) > 200)
    cols = np.nonzero(runs >= HELD_MIN * pitch)[0]
    if len(cols) < 2:
        return None
    groups = np.split(cols, np.nonzero(np.diff(cols) > 1)[0] + 1)
    xs = [float(g.mean()) for g in groups]
    pairs = [(a, b) for i, a in enumerate(xs) for b in xs[i + 1:] if 0.8 * pitch <= b - a <= 1.1 * pitch]
    if len(pairs) != 1:
        return None                                  # none, or ambiguous
    mid = sum(pairs[0]) / 2
    k = int(np.argmin(np.abs(centres - mid)))
    return k + 1 if abs(centres[k] - mid) <= 0.5 * pitch else None


@dataclass
class Hotbar:
    """One read of the hotbar."""
    reads: list
    centres: Optional[np.ndarray] = None
    pitch: float = 0.0
    top: int = 0
    counts: dict = field(default_factory=dict)
    held: Optional[int] = None
    band: Optional[np.ndarray] = None


def read_hotbar(frame: np.ndarray) -> Hotbar:
    from fischequip import fit_hotbar, hotbar_band, hotbar_slot_words, read_hotbar_words

    band = hotbar_band(frame)
    w = frame.shape[1]
    reads = read_hotbar_words(band, w)
    hb = Hotbar(reads=reads, band=band)
    fit = fit_hotbar([x for r in reads for x in r], w)
    if fit is None:
        return hb
    _, geo = hotbar_slot_words([x for r in reads for x in r], w, fit)
    if geo is None:
        return hb
    hb.centres, hb.pitch, hb.top = geo
    for r in reads:
        hb.counts.update(slot_counts(r, hb.centres, hb.pitch))
    hb.held = held_slot(band, hb.centres, hb.pitch, hb.top)
    return hb


# --------------------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------------------


@dataclass
class ItemState:
    uses: int = 0
    count: Optional[int] = None          # last amount read
    status: str = ""
    last_used: Optional[float] = None
    retry_at: float = 0.0


class Useables:
    """Runs between casts (worker thread, rod reeled in). `bot` gives grab(),
    rect, log(), focus, mouse and the quest state."""

    def __init__(self, settings: dict, log: Callable[[str], None],
                 clock: Callable[[], float] = time.time):
        self.cfg = clean_useables(settings)
        self.log = log
        self.clock = clock
        self.state: dict[str, ItemState] = {}
        self.current_bait: Optional[str] = None
        self.bait_count: Optional[int] = None
        self.started = clock()
        self.out_of_bait = False
        totems = {t["name"]: t for t in _index("totems.json", "totems")}
        weather = {w["name"]: w for w in _index("weather.json", "weather")}
        self._totem_info = totems
        self._weather = weather

    @property
    def active(self) -> bool:
        return self.cfg["enabled"] and (bool(self._totems()) or self.cfg["bait"]["manage"])

    def _totems(self) -> list[dict]:
        return [t for t in self.cfg["totems"] if t["on"]]

    def st(self, name: str) -> ItemState:
        return self.state.setdefault(name, ItemState())

    def update(self, settings: dict) -> None:
        """New settings mid-run (saved on the tab): limits apply at once;
        this run's use counts are kept."""
        self.cfg = clean_useables(settings)

    # -- limits -------------------------------------------------------------------
    def limited(self, item: dict) -> Optional[str]:
        """Why `item` must not be used now (its limits), or None."""
        s = self.st(item["name"])
        if item["max_uses"] and s.uses >= item["max_uses"]:
            return f"limit reached ({s.uses}/{item['max_uses']} this run)"
        if s.count is not None and s.count <= item["keep"]:
            return (f"stopped at {s.count} left (keeping {item['keep']}) -- resumes when "
                    f"you have more")
        return None

    # -- totems -------------------------------------------------------------------
    def quest_needs(self, quests) -> set[str]:
        return {m for q in quests or [] for o in q.objectives if not o.done for m in o.mutations}

    def totem_due(self, t: dict, quests, now: float) -> Optional[str]:
        """Why totem `t` should be used now, or None."""
        s = self.st(t["name"])
        gap = t["every_min"] * 60
        if s.retry_at > now:
            return None
        if t["when"] == "start":
            return "start of the run" if s.last_used is None and s.uses == 0 else None
        if s.last_used is not None and now - s.last_used < gap:
            return None
        if t["when"] == "every":
            return f"every {t['every_min']:g} min"
        need = self.quest_needs(quests)
        if not need:
            return None
        info = self._totem_info.get(t["name"], {})
        w = self._weather.get(info.get("weather") or "", {})
        gives = set((w.get("mutations") or {}).keys())
        if "mutation" in t["name"].lower() or w.get("group") == "modifier":
            hit = sorted(need)
        else:
            hit = sorted(need & gives)
        return f"quest needs {', '.join(hit)}" if hit else None

    # -- baits --------------------------------------------------------------------
    def pick_bait(self) -> Optional[dict]:
        for b in self.cfg["bait"]["list"]:
            if b["on"] and self.limited(b) is None and self.st(b["name"]).retry_at <= self.clock():
                return b
        return None

    def note_bait(self, name: Optional[str], count: Optional[int]) -> None:
        if name:
            self.current_bait = name
            if count is not None:
                self.st(name).count = count
        self.bait_count = count if name else self.bait_count

    def cast_done(self) -> None:
        """A cast used one of the current bait."""
        if self.current_bait and self.cfg["bait"]["manage"]:
            s = self.st(self.current_bait)
            s.uses += 1
            if s.count is not None:
                s.count = max(0, s.count - 1)

    # -- the between-casts step ------------------------------------------------------
    def between_casts(self, bot) -> None:
        if not self.cfg["enabled"]:
            return
        now = self.clock()
        if self.cfg["bait"]["manage"] and self.cfg["bait"]["list"]:
            self._baits(bot)
        for t in self._totems():
            why_not = None
            s = self.st(t["name"])
            due = self.totem_due(t, getattr(bot, "quests", []), now)
            if due is None:
                if not s.status or s.status.startswith("next"):
                    s.status = self._next_text(t, now)
                continue
            why_not = self.limited(t)
            if why_not:
                if s.status != why_not:
                    self.log(f"useables: {t['name']} not used -- {why_not}")
                s.status = why_not
                continue
            self.log(f"useables: using {t['name']} ({due})")
            self.use_totem(bot, t)

    def _next_text(self, t: dict, now: float) -> str:
        s = self.st(t["name"])
        if t["when"] == "start":
            return "used at the start" if s.uses else "at the start of the next run"
        if s.last_used is None:
            return "next: now" if t["when"] == "every" else "next: when a quest needs it"
        left = t["every_min"] * 60 - (now - s.last_used)
        return f"next: in {max(0, left) / 60:.0f} min" + (
            " (if a quest needs it)" if t["when"] == "quest" else "")

    def _baits(self, bot) -> None:
        try:
            name, count = read_current_bait(bot.grabber.grab())
        except Exception as exc:
            name, count = None, None
            self.log(f"useables: could not read the current bait ({exc!r})")
        self.note_bait(name, count)
        want = self.pick_bait()
        cur = self.current_bait
        if cur is not None:
            item = next((b for b in self.cfg["bait"]["list"] if b["name"] == cur), None)
            s = self.st(cur)
            s.status = ("in use" + (f" -- {s.count} left" if s.count is not None else "")) \
                if item and self.limited(item) is None else (self.limited(item) if item else "in use (not in your list)")
        if want is None:
            if not self.out_of_bait:
                self.out_of_bait = True
                self.log("useables: every bait in your list is at its limit")
                if self.cfg["bait"]["when_out"] == "stop":
                    self.log("useables: stopping (Useables > Baits: when none is left)")
                    bot.stop()
            return
        self.out_of_bait = False
        if want["name"] == cur:
            return
        self.log(f"useables: equipping bait {want['name']}"
                 + (f" (was {cur})" if cur else ""))
        self.equip_bait(bot, want)

    # -- actions (game input) ----------------------------------------------------------
    def equip_bait(self, bot, b: dict) -> bool:
        from fischequip import EquipmentMenu, MenuError

        s = self.st(b["name"])
        try:
            with EquipmentMenu(bot.grabber.grab, bot.rect, self.log,
                               cancelled=lambda: not bot.running) as menu:
                result, count = menu.equip_bait(b["name"], keep=b["keep"])
        except MenuError as exc:
            s.status, s.retry_at = f"could not equip: {exc}", self.clock() + RETRY_S
            self.log(f"  bait {b['name']}: {exc} -- next try in {RETRY_S // 60} min")
            return False
        except Exception as exc:
            s.status, s.retry_at = f"could not equip: {exc!r}", self.clock() + RETRY_S
            self.log(f"  bait {b['name']}: {exc!r}")
            return False
        finally:
            bot.recentre()
        if count is not None:
            s.count = count
        if result == "reserve":
            s.status = self.limited(b) or f"{count} left"
            self.log(f"  bait {b['name']}: {count} left, keeping {b['keep']} -- skipped")
            return False
        self.current_bait = b["name"]
        s.status = "in use" + (f" -- {s.count} left" if s.count is not None else "")
        self.log(f"  bait {b['name']}: " + ("already equipped" if result == "already" else "equipped")
                 + (f" ({count} left)" if count is not None else ""))
        return True

    def use_totem(self, bot, t: dict) -> bool:
        """Press the totem's slot, check it is in hand, click, take the rod
        back with T. A use counts once the click was made with the totem in
        hand; the amount is re-read to confirm."""
        from fischequip import VK_T, WinInput, find_hotbar_slot

        name, s = t["name"], self.st(t["name"])
        inp = WinInput()
        frame = bot.grabber.grab()
        hb = read_hotbar(frame)
        others = [n for n in self._totem_info if n != name]
        slot, what = find_hotbar_slot(hb.reads, frame.shape[1], name, others)
        if slot is None:
            s.status, s.retry_at = "not in your hotbar", self.clock() + RETRY_S
            self.log(f"  {name}: {what} -- put it in your hotbar; next try in "
                     f"{RETRY_S // 60} min")
            return False
        before = hb.counts.get(slot)
        if before is not None:
            s.count = before
            if before <= t["keep"]:
                s.status = self.limited(t)
                self.log(f"  {name}: {before} left, keeping {t['keep']} -- not used")
                return False
        rod_slot = hb.held
        inp.tap(0x30 + slot % 10)
        time.sleep(0.45)
        hb2 = read_hotbar(bot.grabber.grab())
        if hb2.held != slot:
            # Not in hand (or the frame was not seen): clicking would cast the rod.
            s.status, s.retry_at = "could not take it in hand", self.clock() + RETRY_S
            self.log(f"  {name}: pressed {slot % 10} but slot {hb2.held or '?'} is in hand "
                     f"-- not clicking")
            self._rod_back(bot, inp, rod_slot)
            return False
        cx, cy = bot.rect.left + bot.rect.width // 2, bot.rect.top + int(bot.rect.height * 0.55)
        inp.click(cx, cy)
        s.uses += 1
        s.last_used = self.clock()
        time.sleep(1.2)                                  # the totem's animation
        after = read_hotbar(bot.grabber.grab())
        left = after.counts.get(slot)
        if left is not None:
            s.count = left
        confirmed = before is not None and (left is None or left < before)
        self.log(f"  {name}: used (slot {slot}"
                 + (f", {before} -> {left if left is not None else 0} left" if confirmed
                    else ", amount not confirmed") + f"; {s.uses} this run)")
        s.status = f"used {s.uses}x" + (f" -- {s.count} left" if s.count is not None else "")
        self._rod_back(bot, inp, rod_slot)
        return True

    def _rod_back(self, bot, inp, rod_slot: Optional[int]) -> None:
        """T takes the rod in hand; check the frame moved back to its slot."""
        from fischequip import VK_T

        inp.tap(VK_T)
        time.sleep(0.4)
        hb = read_hotbar(bot.grabber.grab())
        if rod_slot is not None and hb.held is not None and hb.held != rod_slot:
            self.log(f"  rod: slot {hb.held} in hand, the rod was in {rod_slot} -- pressing T again")
            inp.tap(VK_T)
            time.sleep(0.4)
        elif hb.held is None and rod_slot is not None:
            self.log("  rod: nothing in hand after T -- pressing T again")
            inp.tap(VK_T)
            time.sleep(0.4)
        bot.recentre()

    # -- UI ------------------------------------------------------------------------
    def view(self) -> dict:
        return {"enabled": self.cfg["enabled"], "current_bait": self.current_bait,
                "bait_count": self.bait_count, "out_of_bait": self.out_of_bait,
                "items": {k: {"uses": v.uses, "count": v.count, "status": v.status}
                          for k, v in self.state.items()}}
