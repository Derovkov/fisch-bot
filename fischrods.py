"""
Rod profiles: which rod is equipped, and how to play its own minigame if it has one.

The rod list itself is ui/rods.json, extracted from the official Fisch wiki
(https://fischipedia.org/wiki/Fishing_Rods, fetched 2026-10-02): name, where it
is found, how to get it, and base stats. Enchants are ui/enchants.json (from
https://fischipedia.org/wiki/Enchantments). Every rod gets a StandardRod profile
unless SPECIAL below defines one.

Some rods run an extra minigame on top of the normal reel. Such a rod gets a
RodProfile subclass that can

  * detect(frame)   -- is the rod's own minigame on screen right now?
  * play(ctx)       -- one step of playing it (send input via ctx.mouse).

The bot does all the work -- nothing is ever handed to the person playing. While
detect() is True the bot runs this rod's play() code instead of the slider servo;
when it goes False the servo resumes. Rods are added one at a time, from the wiki
plus footage of the minigame -- the same measure-don't-guess approach as the reel
bar (see NOTES.md).

To add a rod: subclass RodProfile, implement detect/play, register it in SPECIAL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

DATA_DIR = Path(__file__).with_name("ui")
DEFAULT_ROD = "Fabulous Rod"     # the user's rod (Starforged Spirit is its enchant)


@dataclass
class RodContext:
    """What a rod's play() gets each step."""
    frame: np.ndarray           # full client-area frame, RGB
    now: float                  # time.perf_counter()
    mouse: object               # fischbot.Mouse: set_down(bool), click(), release()
    log: object                 # callable(str)


class RodProfile:
    name: str = "?"
    notes: str = ""
    wiki: Optional[str] = None
    has_extra: bool = False     # False: never called mid-reel (no extra cost)

    def reset(self) -> None:
        """Called at the start of each reel."""

    def detect(self, frame: np.ndarray) -> bool:
        return False

    def play(self, ctx: RodContext) -> None:
        pass


class StandardRod(RodProfile):
    """Normal reel only -- no rod-specific minigame."""

    def __init__(self, name: str, notes: str = "", wiki: Optional[str] = None):
        self.name = name
        self.notes = notes
        self.wiki = wiki


# Rods whose own minigame the bot knows how to play: name -> RodProfile instance.
SPECIAL: dict[str, RodProfile] = {}


def _load(name: str) -> dict:
    try:
        data = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


ROD_DATA: list[dict] = _load("rods.json").get("rods", [])
ENCHANTS: dict[str, dict] = _load("enchants.json").get("enchants", {})


def rod_names() -> list[str]:
    names = [r["name"] for r in ROD_DATA]
    return names or [DEFAULT_ROD]


def get_rod(name: str) -> RodProfile:
    if name in SPECIAL:
        return SPECIAL[name]
    for r in ROD_DATA:
        if r["name"] == name:
            return StandardRod(name, wiki=r.get("wiki"))
    return StandardRod(name or DEFAULT_ROD)


def rod_control(name: str, enchants: list[str]) -> float:
    """The rod's Control with its enchants (their first stated Control bonus)."""
    import re
    base = next((r.get("control") for r in ROD_DATA if r["name"] == name), None) or 0.0
    for e in enchants:
        m = re.search(r"([+-]\s?[\d.]+)\s*Control", ENCHANTS.get(e, {}).get("effect", ""))
        if m:
            base += float(m.group(1).replace(" ", ""))
    return float(base)


def slider_frac_for(name: str, enchants: list[str]) -> float:
    """Expected slider width, x the track, from Control: measured Duskwire
    (-0.15) 15%, Fabulous Rod (+0.08) ~35%, Lullaby + Herculean (+0.45) ~81%
    -- about 30% + 1.1 x Control."""
    return 0.30 + 1.1 * rod_control(name, enchants)


def reel_enchants(names: list[str]) -> list[str]:
    """The given enchants that change the reel itself (slashes/stuns, fish
    movement, forced progress, control) -- logged at the start of a run."""
    return [n for n in names if ENCHANTS.get(n, {}).get("affects_reel")]


# Kept for callers that iterate profiles (CLI help, older code).
ROD_PROFILES: list[RodProfile] = [get_rod(n) for n in rod_names()]
