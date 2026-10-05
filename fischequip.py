"""
Drive the in-game Equipment Bag (hotkey N): equip a rod, or list every rod the
player owns. Everything is found by READING the screen (Windows OCR,
fischocr.py), using client-relative positions and a closer OCR pass when
small button text is missed:

    * the menu is open when rod cards ("[Rod Name]") or the search box are read;
    * the search box is the line reading "Search..." (remembered once typed in);
    * a rod's card is its "[Rod Name]" line (fischscan.parse_rod_screen), and its
      button is the "Equip" / "Equipped" line below that name.

From the user (2026-10-03): N always opens the rod section (a bait tab sits
beside it); N cannot be opened while the rod is cast, so the bot only does this
between casts, after a reel; the menu has a search box; N again closes it.

Nothing here is saved to disk: screens are read in RAM, and only rod names and
what was found are logged (never other on-screen text, e.g. player names).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from fischocr import _norm, ocr_lines, ocr_words
from fischscan import merge_scan, parse_rod_screen

user32 = ctypes.windll.user32

# Timings (s). Generous: the menu animates in, and search results redraw.
OPEN_WAIT_S = 3.0           # menu must appear within this after N
CLOSE_WAIT_S = 2.0
SEARCH_SETTLE_S = 0.45      # after typing, before reading the results
EQUIP_SETTLE_S = 0.6        # after clicking Equip, before checking "Equipped"
SCROLL_SETTLE_S = 0.35
SCROLL_NOTCHES = 3
SCROLL_TOP_NOTCHES = 15     # per step while going back to the top
SCROLL_MAX_STEPS = 300      # safety cap (263 rods exist)
SCROLL_SAME_PX = 4          # rods moved less than this between reads: not scrolling
OCR_TARGET_W = 3000         # upscale the client to ~this width before OCR

# --------------------------------------------------------------------------------------
# OS input (SendInput with scan codes: Roblox reads keys by scan code)
# --------------------------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _HI(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("ki", _KI), ("mi", _MI), ("hi", _HI)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_WHEEL = 0x0800
VK_BACK, VK_RETURN, VK_SHIFT, VK_CONTROL = 0x08, 0x0D, 0x10, 0x11
VK_N, VK_A = 0x4E, 0x41
VK_T = 0x54                     # Fisch: take the equipped rod in hand


class WinInput:
    """Keyboard and mouse for Roblox, which must be the foreground window."""

    def __init__(self, key_gap_s: float = 0.012):
        self.gap = key_gap_s

    @staticmethod
    def _send(*inputs: _INPUT) -> None:
        arr = (_INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))

    def _key(self, vk: int, up: bool) -> None:
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        self._send(_INPUT(INPUT_KEYBOARD, _U(ki=_KI(0, scan, flags, 0, 0))))

    def tap(self, vk: int, mods: tuple[int, ...] = ()) -> None:
        for m in mods:
            self._key(m, False)
        self._key(vk, False)
        time.sleep(self.gap)
        self._key(vk, True)
        for m in reversed(mods):
            self._key(m, True)
        time.sleep(self.gap)

    def type_text(self, text: str) -> None:
        """Type with the keyboard layout's own keys (letters, digits, space,
        apostrophes...). Characters the layout cannot type are skipped -- the
        game's search matches part of a name anyway."""
        for ch in text:
            code = user32.VkKeyScanW(ord(ch))
            if code == -1 or code & 0xFFFF == 0xFFFF:
                continue
            vk, shift = code & 0xFF, (code >> 8) & 0xFF
            mods = tuple(m for bit, m in ((1, VK_SHIFT), (2, VK_CONTROL)) if shift & bit)
            if shift & 4:                     # AltGr characters: skip
                continue
            self.tap(vk, mods)

    def move(self, x: int, y: int) -> None:
        """Hover at screen coordinates without pressing any button."""
        user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, None)
        user32.mouse_event(MOUSEEVENTF_MOVE, 0xFFFFFFFF, 0, 0, None)

    def click(self, x: int, y: int) -> None:
        """Screen coordinates. Moves first and nudges, so Roblox registers the
        hover before the press."""
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, None)
        user32.mouse_event(MOUSEEVENTF_MOVE, 0xFFFFFFFF, 0, 0, None)    # -1
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)

    def wheel(self, x: int, y: int, notches: int) -> None:
        """notches > 0 scrolls DOWN (towards later items)."""
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.03)
        for _ in range(abs(notches)):
            delta = (-120 if notches > 0 else 120) & 0xFFFFFFFF
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, None)
            time.sleep(0.03)


# --------------------------------------------------------------------------------------
# Reading the menu
# --------------------------------------------------------------------------------------

Box = tuple[int, int, int, int]


@dataclass
class Screen:
    lines: list = field(default_factory=list)       # [(text, box)] client px
    cards: list = field(default_factory=list)       # fischscan cards
    search: Optional[Box] = None

    @property
    def open(self) -> bool:
        return bool(self.cards) or self.search is not None

    def card(self, rod: str) -> Optional[dict]:
        return next((c for c in self.cards if c["rod"] == rod), None)

    def button(self, card: dict) -> Optional[tuple[str, Box]]:
        """("equip" | "equipped", box) of the card's button: the nearest such
        line below the rod name, within the card's columns."""
        x0, _, x1, name_bottom = card["box"]
        best = None
        for text, b in self.lines:
            kind = button_kind(text)
            cx = (b[0] + b[2]) / 2
            if (kind and x0 <= cx <= x1 and b[1] >= name_bottom - 2
                    and b[1] < card.get("bottom", float("inf"))):
                if best is None or b[1] < best[1][1]:
                    best = (kind, b)
        return best


def button_kind(text: str) -> Optional[str]:
    """Short button labels only, including common small-font OCR slips.
    Do not treat 'Equipment Bag' or instructions as an Equip button."""
    label = _norm(text).replace(" ", "")
    if 7 <= len(label) <= 9 and _ratio(label, "equipped") >= .80:
        return "equipped"
    if 4 <= len(label) <= 6 and _ratio(label, "equip") >= .80:
        return "equip"
    return None


# The bag's side panel (Baits / Bobbers / Lanterns tabs) has its own search box,
# "Search Baits..." (the user's screenshot, 2026-10-03: rod switching typed the
# rod name into it). Only the rod list's box is ours; a side-panel box is
# skipped by its placeholder word (OCR-tolerant prefixes: "balts", "bobers").
OTHER_SEARCH = ("ba", "bo", "la", "lu", "to", "ac", "sk", "it", "fi", "em")


def find_search(lines, cards=(), want: str = "rods") -> Optional[Box]:
    """The rod search box: its placeholder text ("Search...", "Search rods..."),
    never the side panel's "Search Baits/Bobbers/Lanterns...". With rod cards
    read, the box over the cards' column wins. want="baits": the side panel's
    "Search Baits..." box instead (bait changing, the Useables tab)."""
    hits = []
    for t, b in lines:
        words = _norm(t).split()
        if not words or not words[0].startswith("search"):
            continue
        rest = words[1:] or ([words[0][6:]] if len(words[0]) > 6 else [])
        other = bool(rest and rest[0] and not rest[0].startswith("ro")
                     and rest[0].startswith(OTHER_SEARCH))
        if want == "baits":
            if other and rest[0].startswith("ba"):
                hits.append(b)
            continue
        if other:
            continue                                 # baits, bobbers, lanterns ...
        hits.append(b)
    if not hits:
        return None
    if cards:
        cx = np.median([(c["box"][0] + c["box"][2]) / 2 for c in cards])
        return min(hits, key=lambda b: (abs((b[0] + b[2]) / 2 - cx), b[1]))
    return min(hits, key=lambda b: b[1])


COUNT_RE = re.compile(r"^[x×][\dlIO,]{1,8}$")


def read_bait_cards(lines, names: list[str]) -> list[dict]:
    """Bait cards of the bag's side panel: "[Instant Catcher]" with its amount
    ("x15823") under the name and the [Equip]/[Equipped] button below that
    (the user's screenshot, 2026-10-03). `rod` holds the bait name so
    Screen.card / Screen.button work on these too."""
    from fischocr import best_match

    out = []
    for text, box in lines:
        if "[" not in text:
            continue
        inner = re.sub(r"^[^A-Za-z]*|[^A-Za-z']*$", "", text)
        name = best_match(inner, names, 0.8)
        if not name:
            continue
        h = box[3] - box[1]
        cx = (box[0] + box[2]) / 2
        count = None
        for t, b in lines:
            if (COUNT_RE.match(t.strip()) and 0 <= b[1] - box[1] <= 3 * h
                    and abs((b[0] + b[2]) / 2 - cx) <= max(box[2] - box[0], 4 * h)):
                from fischuse import _count
                count = _count(t)
                break
        out.append({"rod": name, "box": box, "count": count, "equipped": False})
    return out


def read_screen(frame: np.ndarray) -> Screen:
    scale = float(np.clip(OCR_TARGET_W / max(1, frame.shape[1]), 1.2, 2.6))
    lines = ocr_lines(frame, scale=scale)
    cards = parse_rod_screen(frame, lines)
    return Screen(lines, cards, find_search(lines, cards))


# --- rod skins: the pen button on a rod card opens "<Rod> Skins" ---------------
# From the user (2026-10-05): the pen button on the rod's card opens its skin
# list (cards: skin name, then [Equip] / [Equipped]; "Default" first, [Back] top
# right). The list does NOT close by itself -- left open it would break the next
# rod switch -- so the bot always presses [Back]. A non-default skin changes the
# reel bar (see fischtrack's skin tracker); the default bar reads far better.
SKIN_EDIT_ICON = Path(__file__).with_name("ui") / "icons" / "game" / "skin_edit.png"
SKIN_EDIT_MIN = 0.62            # template match score for the pen button
SKIN_EDIT_SCALES = tuple(round(0.55 + 0.05 * i, 2) for i in range(26))   # 0.55-1.80
SKIN_SETTLE_S = 0.6             # after clicking the pen / Equip / Back


def _edit_icon():
    import cv2
    t = cv2.imread(str(SKIN_EDIT_ICON), cv2.IMREAD_GRAYSCALE)
    if t is None:
        raise MenuError(f"missing {SKIN_EDIT_ICON.name}")
    # Only the icon's white strokes and the pixels right around them count: the
    # square behind them takes the colour of each rod's card art.
    strokes = (t > 110).astype(np.uint8)
    mask = cv2.dilate(strokes, np.ones((3, 3), np.uint8))
    return t, mask


def find_skin_edit(frame: np.ndarray, region: Box) -> Optional[tuple[Box, float]]:
    """The pen (skins) button inside `region` of an RGB client frame:
    (box, score), or None. Searched over UI scales (window sizes)."""
    import cv2
    x0, y0, x1, y1 = (max(0, int(v)) for v in region)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2GRAY)
    t, m = _edit_icon()
    best = None
    for k in SKIN_EDIT_SCALES:
        tw, th = max(8, round(t.shape[1] * k)), max(8, round(t.shape[0] * k))
        if tw > gray.shape[1] or th > gray.shape[0]:
            break
        ts = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
        ms = cv2.resize(m, (tw, th), interpolation=cv2.INTER_NEAREST)
        res = cv2.matchTemplate(gray, ts, cv2.TM_CCOEFF_NORMED, mask=ms)
        res = np.nan_to_num(res, nan=-1, posinf=-1, neginf=-1)
        _, score, _, (bx, by) = cv2.minMaxLoc(res)
        if best is None or score > best[1]:
            best = ((x0 + bx, y0 + by, x0 + bx + tw, y0 + by + th), float(score))
    return best if best is not None and best[1] >= SKIN_EDIT_MIN else None


def skin_screen(lines) -> Optional[dict]:
    """The skin list, if it is what's on screen: {"default": box, "button":
    ("equip" | "equipped", box) or None, "back": box}."""
    title = any(_norm(t).endswith(" skins") for t, _ in lines)
    back = next((b for t, b in lines if _ratio(_norm(t).strip("[] "), "back") >= .8
                 and len(_norm(t)) <= 8), None)
    default = next((b for t, b in lines if _ratio(_norm(t), "default") >= .85), None)
    if not (title or back) or default is None:
        return None
    cx = (default[0] + default[2]) / 2
    width = max(default[2] - default[0], 1)
    below = [(button_kind(t), b) for t, b in lines
             if button_kind(t) and b[1] > default[3]
             and abs((b[0] + b[2]) / 2 - cx) < 2.5 * width]
    button = min(below, key=lambda kb: kb[1][1] - default[3], default=None)
    return {"default": default, "button": button, "back": back}


def skin_button_closeup(frame: np.ndarray, default: Box) -> Optional[tuple[str, Box]]:
    """The button under "Default", read at higher magnification: its small
    green [Equip] text was missed by the whole-screen read (user screenshot,
    2026-10-05), only the bright [Equipped] was read."""
    h, w = frame.shape[:2]
    cx, dw = (default[0] + default[2]) / 2, max(default[2] - default[0], 20)
    x0, x1 = int(max(0, cx - 2.5 * dw)), int(min(w, cx + 2.5 * dw))
    y0, y1 = int(default[3]), int(min(h, default[3] + max(8 * dw / 3, h * .2)))
    crop = frame[y0:y1, x0:x1]
    if not crop.size:
        return None
    for factor in (3., 5.):
        scale = min(factor, 4000 / max(crop.shape[:2]))
        lines = [(t, (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0))
                 for t, b in ocr_lines(crop, scale=scale)]
        kinds = [(button_kind(t), b) for t, b in lines if button_kind(t)]
        if kinds:
            return min(kinds, key=lambda kb: kb[1][1])
    return None


def card_art_region(card: dict, h: int) -> Box:
    """The part of a rod card around its art (above the name), where the pen
    and the Lullaby's mode buttons sit. `h`: the client height."""
    x0, _, x1, _ = card["box"]
    nb = card["name_box"]
    return (x0, max(0, nb[1] - int(h * .5)), x1,
            min(card.get("bottom", h), nb[3] + int(h * .2), h))


# --- Lullaby: its six mode buttons on the rod card -----------------------------
# From the user (2026-10-05, screenshot of the bag): the Lullaby's card has a
# column of six small coloured buttons down its right side -- gold, cyan,
# orange, green, peach, pink from the top -- one per mode (fischlullaby.MODES,
# same order). The whole column is matched as one picture: six icons at fixed
# spacing are far more distinctive than any one of them, and the match gives
# the UI scale too. The strip was cut from that screenshot.
LULLABY_MODES_ICON = Path(__file__).with_name("ui") / "icons" / "game" / "lullaby_modes.png"
LULLABY_MODE_Y = (11, 39, 66, 95, 122, 151)   # button centres in the strip (px)
LULLABY_MODE_X = 12
LULLABY_MODE_HALF = 8                         # half a button, strip px
LULLABY_MODES_MIN = 0.55                      # template match score for the column
LULLABY_SCALES = tuple(round(0.5 + 0.05 * i, 2) for i in range(31))   # 0.50-2.00
LULLABY_SETTLE_S = 0.6                        # after clicking a mode button


def _modes_strip():
    import cv2
    t = cv2.imread(str(LULLABY_MODES_ICON))
    if t is None:
        raise MenuError(f"missing {LULLABY_MODES_ICON.name}")
    rgb = t[:, :, ::-1].astype(np.int16)
    mx, mn = rgb.max(2), rgb.min(2)
    # The coloured strokes (and the pale ones) and a pixel around them; the
    # card art behind them differs with the skin.
    strokes = (((mx >= 100) & (mx - mn >= 45)) | (mn >= 150)).astype(np.uint8)
    mask = cv2.dilate(strokes, np.ones((3, 3), np.uint8))
    return np.ascontiguousarray(t[:, :, ::-1]), mask


def find_lullaby_modes(frame: np.ndarray, region: Box) -> Optional[tuple[list[Box], float]]:
    """The six mode buttons inside `region` of an RGB client frame, top to
    bottom: ([box, ...], score), or None. Searched over UI scales."""
    import cv2
    x0, y0, x1, y1 = (max(0, int(v)) for v in region)
    crop = np.ascontiguousarray(frame[y0:y1, x0:x1, :3])
    if crop.size == 0:
        return None
    t, m = _modes_strip()

    def match(k):
        tw, th = max(6, round(t.shape[1] * k)), max(30, round(t.shape[0] * k))
        if tw > crop.shape[1] or th > crop.shape[0]:
            return None
        ts = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
        ms = cv2.resize(m, (tw, th), interpolation=cv2.INTER_NEAREST)
        res = cv2.matchTemplate(crop, ts, cv2.TM_CCOEFF_NORMED,
                                mask=np.repeat(ms[:, :, None], 3, 2))
        res = np.nan_to_num(res, nan=-1, posinf=-1, neginf=-1)
        _, score, _, (bx, by) = cv2.minMaxLoc(res)
        return k, float(score), bx, by

    # Every other scale first, then the neighbours of the best (half the time).
    hits = [h for h in map(match, LULLABY_SCALES[::2]) if h]
    if not hits:
        return None
    best = max(hits, key=lambda h: h[1])
    i = LULLABY_SCALES.index(best[0])
    for j in (i - 1, i + 1):
        h = match(LULLABY_SCALES[j]) if 0 <= j < len(LULLABY_SCALES) else None
        if h and h[1] > best[1]:
            best = h
    if best[1] < LULLABY_MODES_MIN:
        return None
    k, score, bx, by = best
    cx, r = x0 + bx + LULLABY_MODE_X * k, LULLABY_MODE_HALF * k
    boxes = [(int(cx - r), int(y0 + by + (y - LULLABY_MODE_HALF) * k),
              int(cx + r), int(y0 + by + (y + LULLABY_MODE_HALF) * k)) for y in LULLABY_MODE_Y]
    return boxes, score


def centre(b: Box) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


class MenuError(RuntimeError):
    pass


class MenuCancelled(MenuError):
    pass


class EquipmentMenu:
    """One use of the Equipment Bag: open it, do things, close it (always).

        with EquipmentMenu(grab, rect, log) as menu:
            menu.equip("Duskwire")

    `grab()` returns the Roblox client as RGB; `rect` is the client rect in
    screen coordinates (left/top). Roblox must be the foreground window."""

    def __init__(self, grab: Callable[[], np.ndarray], rect, log: Callable[[str], None],
                 inp: Optional[WinInput] = None, reader: Callable = read_screen,
                 cancelled: Callable[[], bool] = lambda: False):
        self.grab, self.rect, self.log = grab, rect, log
        self.inp = inp or WinInput()
        self.reader = reader
        self.cancelled = cancelled
        self.search_box: Optional[Box] = None
        self.kind = "rods"              # which list's search box is ours: rods | baits
        self.typed = False              # the search box holds our text ...
        self._last_len = 0              # ... this many characters of it
        self.opened_by_us = False
        self._closing = False

    # -- context ------------------------------------------------------------------
    def __enter__(self) -> "EquipmentMenu":
        try:
            self.open()
        except MenuCancelled:
            # __exit__ isn't called if cancellation interrupts __enter__.
            if self.opened_by_us or self.typed:
                self.__exit__()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.close()
        except Exception as e:                       # never mask the real error
            self.log(f"  equipment bag: could not confirm it closed ({e})")

    # -- primitives -----------------------------------------------------------------
    def _check(self):
        if not self._closing and self.cancelled():
            raise MenuCancelled("cancelled")

    def _pause(self, seconds):
        self._check()
        while seconds > 0:
            step = min(seconds, .05)
            time.sleep(step)
            seconds -= step
            self._check()

    def read(self) -> Screen:
        self._check()
        s = self.reader(self.grab())
        self._check()
        box = s.search if self.kind == "rods" else find_search(s.lines, (), self.kind)
        if box is not None:
            self.search_box = box
        return s

    def _abs(self, x: int, y: int) -> tuple[int, int]:
        return self.rect.left + x, self.rect.top + y

    def click(self, b: Box) -> None:
        self._check()
        self.inp.click(*self._abs(*centre(b)))

    def _wait(self, want_open: bool, limit: float) -> Optional[Screen]:
        t_end = time.perf_counter() + limit
        while True:
            s = self.read()
            if s.open == want_open:
                return s
            if time.perf_counter() > t_end:
                return None
            self._pause(0.15)

    def open(self) -> Screen:
        s = self.read()
        if s.open:
            return s
        self._check()
        self.opened_by_us = True
        self.inp.tap(VK_N)
        s = self._wait(True, OPEN_WAIT_S)
        if s is None:
            raise MenuError("the Equipment Bag did not open after pressing N (is the "
                            "rod still cast, or is a chat box focused?)")
        self.opened_by_us = True
        return s

    def close(self) -> None:
        self._closing = True
        try:
            self._close()
        finally:
            self._closing = False

    def _close(self) -> None:
        if self.typed:
            self.clear_search()
        for attempt in range(2):
            if not self.read().open:
                return
            self.inp.tap(VK_N)
            if self._wait(False, CLOSE_WAIT_S) is not None:
                return
        raise MenuError("the Equipment Bag is still open")

    def set_search(self, text: str) -> Screen:
        if self.search_box is None:
            raise MenuError("no search box was read on the rod screen")
        self.click(self.search_box)
        # Mark the focused search immediately so Stop can clear/release it
        # even if it interrupts typing halfway through a name.
        self.typed = True
        self._pause(0.08)
        self._check()
        self.inp.tap(VK_A, (VK_CONTROL,))
        # Ctrl+A then one Backspace clears anything; in case the box does not
        # support Ctrl+A, also delete what we typed last time.
        for _ in range(1 + (self._last_len + 2 if self.typed else 0)):
            self._check()
            self.inp.tap(VK_BACK)
        if text:
            self._last_len = 0
            for ch in text:
                self._check()
                self.inp.type_text(ch)
                self._last_len += 1
        # Enter releases the box, so the next N closes the menu instead of
        # typing an "n" into the search.
        self._check()
        self.inp.tap(VK_RETURN)
        self.typed, self._last_len = bool(text), len(text)
        self._pause(SEARCH_SETTLE_S)
        return self.read()

    def clear_search(self) -> None:
        self.set_search("")
        self.typed = False

    # -- tasks --------------------------------------------------------------------
    def equip(self, rod: str) -> str:
        """Equip `rod`. Returns "equipped" or "already"; raises MenuError."""
        s = self.read()
        if self.search_box is not None:
            s = self.set_search(rod)
        card = s.card(rod)
        if card is None:
            raise MenuError(f"no [{rod}] card was read on the rod screen"
                            + ("" if self.search_box else " (and no search box)"))
        btn = self._button(s, card)
        if card["equipped"] or (btn and btn[0] == "equipped"):
            return "already"
        if btn is None:
            raise MenuError(f"found [{rod}] but no Equip button under it")
        self.click(btn[1])
        self._pause(EQUIP_SETTLE_S)
        after = self.read()
        card2 = after.card(rod)
        btn2 = self._button(after, card2) if card2 else None
        if card2 is not None and (card2["equipped"] or (btn2 and btn2[0] == "equipped")):
            return "equipped"
        raise MenuError(f"clicked Equip on [{rod}] but it does not read Equipped")

    def default_skin(self, rod: str) -> str:
        """Put `rod` on its Default skin: pen button on its card -> Default's
        [Equip] -> [Back]. Returns "default" or "already"; raises MenuError.
        [Back] is pressed whatever happens once the skin list is open."""
        s = self.read()
        if self.search_box is not None:
            s = self.set_search(rod)
        card = s.card(rod)
        if card is None:
            raise MenuError(f"no [{rod}] card was read on the rod screen")
        frame = self.grab()
        pen = find_skin_edit(frame, card_art_region(card, frame.shape[0]))
        if pen is None:
            raise MenuError(f"no skins (pen) button found on the [{rod}] card")
        self.click(pen[0])
        self._pause(SKIN_SETTLE_S)
        try:
            sk = skin_screen(self.read().lines)
            if sk is None:
                self._pause(SKIN_SETTLE_S)
                sk = skin_screen(self.read().lines)
            if sk is None:
                raise MenuError(f"the skin list did not open for [{rod}]")
            if sk["button"] is None:
                sk["button"] = skin_button_closeup(self.grab(), sk["default"])
            if sk["button"] is None:
                raise MenuError("no Equip button under the Default skin")
            if sk["button"][0] == "equipped":
                return "already"
            self.click(sk["button"][1])
            self._pause(SKIN_SETTLE_S)
            after = skin_screen(self.read().lines)
            if after is not None and after["button"] is None:
                after["button"] = skin_button_closeup(self.grab(), after["default"])
            if after is None or after["button"] is None or after["button"][0] != "equipped":
                raise MenuError("clicked Equip on the Default skin but it does not read Equipped")
            return "default"
        finally:
            self._skin_back()

    def lullaby_mode(self, index: int, rod: str = "Lullaby") -> str:
        """Press mode button `index` (0 = top, fischlullaby.MODES order) on the
        Lullaby's card. Returns "changed" when the buttons looked different
        afterwards (the pressed one lit up), else "pressed": the game shows no
        mode name in the bag to read back. Raises MenuError."""
        s = self.read()
        if self.search_box is not None:
            s = self.set_search(rod)
        card = s.card(rod)
        if card is None:
            raise MenuError(f"no [{rod}] card was read on the rod screen (do you own it?)")
        frame = self.grab()
        x0, _, x1, y1 = card_art_region(card, frame.shape[0])
        # The right half of the card, top to just below the name: the buttons
        # hug the right edge (a little past the column OCR gives), and how high
        # they sit above the name is not measured yet.
        region = (x0 + (x1 - x0) // 2, 0, min(frame.shape[1], x1 + (x1 - x0) // 20), y1)
        found = find_lullaby_modes(frame, region)
        if found is None:
            raise MenuError(f"no mode buttons found on the [{rod}] card")
        boxes, score = found
        bx0, by0 = boxes[0][0], boxes[0][1]
        bx1, by1 = boxes[-1][2], boxes[-1][3]
        before = frame[by0:by1, bx0:bx1].astype(np.int16)
        self.log(f"  {rod}: mode buttons found (match {score:.2f}); pressing #{index + 1}")
        self.click(boxes[index])
        self._pause(LULLABY_SETTLE_S)
        after = self.grab()[by0:by1, bx0:bx1].astype(np.int16)
        if before.size and before.shape == after.shape and np.abs(after - before).mean() > 2:
            return "changed"
        return "pressed"

    def _skin_back(self) -> None:
        """Leave the skin list with [Back] (it never closes by itself)."""
        prev, self._closing = self._closing, True       # also when cancelled
        try:
            for _ in range(3):
                sk = skin_screen(self.read().lines)
                if sk is None:
                    return
                if sk["back"] is None:
                    raise MenuError("the skin list is open but no [Back] button was read")
                self.click(sk["back"])
                self._pause(SKIN_SETTLE_S)
            raise MenuError("the skin list did not close with [Back]")
        finally:
            self._closing = prev

    def _button(self, screen: Screen, card: dict):
        btn = screen.button(card)
        if btn is not None:
            return btn
        # Whole-client OCR misses tiny Equip text in narrow/tall windows.
        # OCR just the card footer at higher magnification, preserving client
        # coordinates. Never guess a click when no button text is read.
        if self.cancelled():
            raise MenuError("cancelled")
        frame = self.grab()
        if frame is None:  # injected, text-only test readers
            return None
        x0, _, x1, bottom = card["box"]
        x0, x1 = max(0, x0), min(frame.shape[1], x1)
        y0 = max(0, bottom - 3)
        crop = frame[y0:card.get("bottom", frame.shape[0]), x0:x1]
        if not crop.size:
            return None
        for factor in (4., 6.):
            scale = min(factor, 4000 / max(crop.shape[:2]))
            lines = [(t, (b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0))
                     for t, b in ocr_lines(crop, scale=scale)]
            btn = Screen(lines).button(card)
            if btn:
                self.log(f"  equipment: {btn[0]} button read in card footer at "
                         f"{centre(btn[1])} (client pixels)")
                return btn
        return None

    def equip_bait(self, bait: str, keep: int = 0) -> tuple[str, Optional[int]]:
        """Equip `bait` from the side panel's Baits tab. Returns (result,
        amount): "equipped" / "already", or "reserve" when the card shows
        `keep` or fewer left (not equipped). Raises MenuError."""
        from fischuse import _index

        names = [b["name"] for b in _index("baits.json", "baits")] or [bait]
        s = self.read()
        tab = next((b for t, b in s.lines if _norm(t) == "baits"), None)
        if tab is not None:
            self.click(tab)                      # the Baits tab of the side panel
            time.sleep(0.35)
        self.kind, self.search_box = "baits", None
        s = self.read()
        if self.search_box is None:
            raise MenuError("no 'Search Baits...' box was read in the bag's side panel")
        s = self.set_search(bait)
        cards = read_bait_cards(s.lines, names)
        card = next((c for c in cards if c["rod"] == bait), None)
        if card is None:
            raise MenuError(f"no [{bait}] card in the Baits tab (none left?)")
        if card["count"] is not None and card["count"] <= keep:
            return "reserve", card["count"]
        btn = s.button(card)
        if btn and btn[0] == "equipped":
            return "already", card["count"]
        if btn is None:
            raise MenuError(f"found [{bait}] but no Equip button under it")
        self.click(btn[1])
        time.sleep(EQUIP_SETTLE_S)
        after = self.read()
        card2 = next((c for c in read_bait_cards(after.lines, names) if c["rod"] == bait), None)
        btn2 = after.button(card2) if card2 else None
        if btn2 and btn2[0] == "equipped":
            return "equipped", card["count"]
        raise MenuError(f"clicked Equip on [{bait}] but it does not read Equipped")

    def scan_by_search(self, rods: list[str],
                       progress: Callable[[int, int, int], None] = lambda *a: None
                       ) -> dict[str, dict]:
        """Type every known rod name into the search: slow, but each owned rod
        gets a clean, uncluttered card. Returns {rod: {enchants, equipped}}."""
        if self.search_box is None:
            raise MenuError("no search box was read on the rod screen")
        found: dict[str, dict] = {}
        for i, rod in enumerate(rods):
            if self.cancelled():
                break
            try:
                s = self.set_search(rod)
            except MenuCancelled:
                break
            merge_scan(found, [c for c in s.cards if c["rod"] == rod])
            progress(i + 1, len(rods), len(found))
        return found

    def scan_by_scroll(self, progress: Callable[[int, int, int], None] = lambda *a: None
                       ) -> dict[str, dict]:
        """Scroll the rod list with the mouse wheel, reading every screenful,
        until the list stops moving: the end. Fast; a rod only half on screen
        can be missed or read with fewer enchants (the search mode cannot).

        The end is judged by the rods themselves, as the user suggested: after
        a scroll, rods that were on the previous screen have not moved. (It
        used to stop after two screens with no NEW rod -- live (2026-10-03)
        that ended far too early, when a small scroll showed the same rods.)
        Before calling it the end, the wheel is tried once more over another
        card, in case it was turned outside the list."""
        if self.typed:
            self.clear_search()
        s = self.read()
        if not s.cards:
            raise MenuError("no rod cards were read on the rod screen")
        # To the top: scroll up until nothing moves.
        for _ in range(SCROLL_MAX_STEPS):
            if self.cancelled():
                break
            try:
                s2 = self._scroll(s, -SCROLL_TOP_NOTCHES)
            except MenuCancelled:
                return {}
            if not _moved(s, s2):
                break
            s = s2
        found: dict[str, dict] = {}
        merge_scan(found, s.cards)
        progress(1, 0, len(found))
        for step in range(SCROLL_MAX_STEPS):
            if self.cancelled():
                break
            try:
                s2 = self._scroll(s, SCROLL_NOTCHES)
            except MenuCancelled:
                break
            merge_scan(found, s2.cards)
            progress(step + 2, 0, len(found))
            if _moved(s, s2):
                s = s2
            else:
                break                                # the end of the list
        return found

    def _scroll(self, s: Screen, notches: int) -> Screen:
        """Wheel over the cards of `s`, read; if nothing moved, try once more
        over a different card (the first spot may be outside the list)."""
        spots = [self._cards_centre(s)]
        if s.cards:
            b = s.cards[0]["box"]
            spots.append(((b[0] + b[2]) // 2, b[3]))
        s2 = s
        for x, y in dict.fromkeys(spots):
            self._check()
            self.inp.wheel(*self._abs(x, y), notches)
            self._pause(SCROLL_SETTLE_S)
            s2 = self.read()
            if _moved(s, s2):
                break
        return s2

    @staticmethod
    def _cards_centre(s: Screen) -> tuple[int, int]:
        if not s.cards:
            return 0, 0
        xs = [(c["box"][0] + c["box"][2]) / 2 for c in s.cards]
        ys = [c["box"][3] for c in s.cards]
        return int(np.mean(xs)), int(np.mean(ys))


# --------------------------------------------------------------------------------------
# Hotbar: equipping in the bag puts the rod in the hotbar, not in your hands
# --------------------------------------------------------------------------------------
#
# Live (2026-10-03): the switch equipped the rod in the Equipment Bag, but the
# player still held nothing, so the next cast did nothing. The user: find the
# rod in the hotbar and press the number of its slot.
#
# What the hotbar looks like (saved_logs/20261003_135047/hotbar_0016.25.png,
# 1920 wide): a row of 9 square slots centred on the screen, ~69px apart, each
# with a tiny number top-left and its item's name in small text, wrapped over
# up to four lines -- the rod's label carries its enchant first: "Starforged
# Spirit Fabulous Rod". OCR reads the numbers rarely and garbles the names
# ("Fawtovs"), so slot numbers come from GEOMETRY: the labels are centred in
# their slots and the row is centred on the client, which fits one pitch and
# count. Each slot's words are then matched against the rod's name (the
# enchant removed). As a second clue, the slot whose picture changed while
# the bag equipped the rod is very likely it (the user's idea: look at the
# slots themselves when no number can be read).

HOTBAR_TOP_FRAC = 0.75          # hotbar band: this fraction of the height down
HOTBAR_OCR_SCALES = (2.5, 3.0)  # at 1920 wide; both are read, words pooled
HOTBAR_PITCH_FRAC = (0.022, 0.06)   # slot spacing, fraction of client width (0.036)
HOTBAR_NAME_MIN = 0.6           # rod-name match needed (OCR garbles; 0.72 seen)
HOTBAR_CHANGED_BONUS = 0.25     # score added to the slot that changed during the equip
HOTBAR_CHANGED_RATIO = 2.0      # ... if it changed this much more than the others
# Other things that sit in the hotbar (the user's, 2026-10-03): a slot reading
# more like one of these is not a rod. "Velocity Coil" read "velocity cod",
# close to "Volcanic Rod"; "Equipment" close to "Requiem".
HOTBAR_ITEMS = ("Equipment Bag", "Bestiary", "Velocity Coil", "Pogo Stick", "Tidebreaker",
                "Traveler's Whistle", "Quest Book", "Mutation Totem")


def _ratio(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def _window_score(words: list[str], name: str) -> float:
    """Best match of `name` against any run of words, spaces ignored: OCR
    splits small text mid-word ("f abvtovs rod" for "Fabulous Rod")."""
    n = _norm(name).replace(" ", "")
    best = 0.0
    for size in range(1, len(_norm(name).split()) + 3):
        for i in range(max(1, len(words) - size + 1)):
            chunk = "".join(words[i:i + size])
            # a short scrap must not pass for a long name: "cod" (Velocity
            # Coil's slot) read 0.6 like "crewrod"
            short = min(1.0, len(chunk) / max(1, len(n))) ** 0.5
            best = max(best, _ratio(chunk, n) * short)
    return best


def _strip_enchants(words: list[str], enchants) -> list[str]:
    """Drop an enchant's words from a rod label ("starforged spirit fabulous
    rod"): "spirit" alone made it read like the rod "Spirit of the Forest"."""
    words = list(words)
    for e in enchants:
        ew = _norm(e).split()
        if not ew:
            continue
        for i in range(len(words) - len(ew) + 1):
            if _ratio(" ".join(words[i:i + len(ew)]), " ".join(ew)) >= 0.8:
                del words[i:i + len(ew)]
                break
    return words


def fit_hotbar(words, width: int) -> Optional[tuple[np.ndarray, float]]:
    """(slot centres, pitch) that best explain where the label words sit:
    n slots, pitch p, centred on the client. Words near a slot centre score;
    slots with no word cost one (stops the fit from inventing slots)."""
    xs = np.array([(b[0] + b[2]) / 2 for _, b in words
                   if 0.15 * width <= (b[0] + b[2]) / 2 <= 0.85 * width])
    if len(xs) < 2:
        return None
    best = None
    lo, hi = HOTBAR_PITCH_FRAC
    # Roblox's backpack can retain ~69px slots in a narrow, tall client;
    # its scale need not follow the Fisch reel's width scale.
    for p in np.arange(max(12, lo * width), max(80, hi * width), 0.5):
        for n in range(1, 11):
            if (n - 1) * p + p > width:
                continue
            cs = width / 2 + p * (np.arange(1, n + 1) - (n + 1) / 2)
            d = np.abs(xs[:, None] - cs[None, :])
            near = d.min(1) <= 0.25 * p
            score = int(near.sum()) - (n - len(set(d.argmin(1)[near])))
            quality = (score, -float(d.min(1).mean()))
            if best is None or quality > best[0]:
                best = (quality, cs, p)
    return best[1], best[2]


def hotbar_slot_words(words, width: int, fit=None
                      ) -> tuple[Optional[list[list[str]]], Optional[tuple]]:
    """(words per slot left to right in reading order, (centres, pitch, top
    row of the labels)) -- label row only. `fit`: geometry from fit_hotbar
    (default: fitted to these words)."""
    fit = fit or fit_hotbar(words, width)
    if fit is None:
        return None, None
    cs, p = fit
    near = [(t, b) for t, b in words
            if np.abs(cs - (b[0] + b[2]) / 2).min() <= 0.45 * p]
    if not near:
        return None, None
    # The label row: around the words' median height. The catch / "Equipped
    # ...!" captions above the hotbar sit well outside one slot height.
    ym = float(np.median([(b[1] + b[3]) / 2 for _, b in near]))
    row = [(t, b) for t, b in near if abs((b[1] + b[3]) / 2 - ym) <= 0.75 * p]
    h = float(np.median([b[3] - b[1] for _, b in row])) if row else 1.0
    slots: list[list] = [[] for _ in cs]
    for t, b in row:
        slots[int(np.argmin(np.abs(cs - (b[0] + b[2]) / 2)))].append((t, b))
    out = []
    for sl in slots:
        sl.sort(key=lambda w: (round((w[1][1] + w[1][3]) / 2 / max(1.0, 0.7 * h)), w[1][0]))
        out.append([w for t, _ in sl for w in _norm(t).split()])
    top = min(b[1] for _, b in row) if row else 0
    return out, (cs, p, top)


def slot_changes(before: np.ndarray, after: np.ndarray, centres: np.ndarray,
                 pitch: float, top: int) -> np.ndarray:
    """Mean picture change of each slot (band coordinates)."""
    out = []
    y0, y1 = max(0, int(top - 0.3 * pitch)), int(top + 0.9 * pitch)
    for c in centres:
        x0, x1 = max(0, int(c - 0.45 * pitch)), int(c + 0.45 * pitch)
        a = before[y0:y1, x0:x1].astype(np.int16)
        b = after[y0:y1, x0:x1].astype(np.int16)
        out.append(float(np.abs(a - b).mean()) if a.size and a.shape == b.shape else 0.0)
    return np.array(out)


def find_hotbar_slot(reads, width: int, rod: str, others: list[str],
                     enchants=(), before: Optional[np.ndarray] = None,
                     after: Optional[np.ndarray] = None) -> tuple[Optional[int], str]:
    """(slot number 1.., or None, what was read) for `rod`. `reads`: OCR words
    of the band, one list per OCR scale (scored separately, best kept --
    pooling them doubled the fragments of a split word)."""
    reads = [r for r in reads if r]
    if not reads:
        return None, "no hotbar labels read"
    fit = fit_hotbar([w for r in reads for w in r], width)
    if fit is None:
        return None, "no hotbar labels read"
    per_read = [hotbar_slot_words(r, width, fit) for r in reads]
    per_read = [(sl, geo) for sl, geo in per_read if sl]
    if not per_read:
        return None, "no hotbar labels read"
    cs, p, top = per_read[0][1]
    n = len(cs)
    scores, labels = np.zeros(n), [""] * n
    for sl, _ in per_read:
        for k, ws in enumerate(sl):
            ws = _strip_enchants(ws, enchants)
            mine = _window_score(ws, rod)
            sc = mine
            if mine >= 0.8 * HOTBAR_NAME_MIN:      # only plausible slots: 263 rods
                rival = max((_window_score(ws, o)
                             for o in list(others) + list(HOTBAR_ITEMS) if o != rod),
                            default=0.0)
                # A label that reads clearly more like another rod is not ours.
                if mine + 0.05 < rival:
                    sc = mine * 0.5
            if sc > scores[k]:
                scores[k], labels[k] = sc, " ".join(sl[k])
    changed = None
    if before is not None and after is not None and before.shape == after.shape:
        ch = slot_changes(before, after, cs, p, top)
        if n > 1:
            k = int(np.argmax(ch))
            rest = float(np.median(np.delete(ch, k)))
            if ch[k] > HOTBAR_CHANGED_RATIO * max(rest, 1.0):
                changed = k
                scores[k] += HOTBAR_CHANGED_BONUS
    k = int(np.argmax(scores))
    label = labels[k] or "(no text)"
    note = f"; slot {changed + 1} changed" if changed is not None else ""
    if scores[k] < HOTBAR_NAME_MIN:
        return None, (f"no slot reads like {rod} (best: slot {k + 1} '{label}' "
                      f"{scores[k]:.2f}{note}; {n} slots)")
    return k + 1, (f"slot {k + 1} of {n} reads '{label}'"
                   + (" and changed" if changed == k else ""))


def hotbar_band(frame: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(frame[int(frame.shape[0] * HOTBAR_TOP_FRAC):])


def read_hotbar_words(band: np.ndarray, width: int) -> list[list]:
    """OCR words of the hotbar band, one list per scale."""
    return [ocr_words(band, scale=sc * 1920 / max(1, width)) for sc in HOTBAR_OCR_SCALES]


def select_hotbar_rod(grab: Callable[[], np.ndarray], rod: str, others: list[str],
                      inp: "WinInput", log: Callable[[str], None],
                      enchants=(), before: Optional[np.ndarray] = None,
                      save_crop: Optional[Callable[[np.ndarray], None]] = None) -> bool:
    """Press the number key of the hotbar slot holding `rod`. Only call right
    after the bag equipped it: pressing the slot of a rod already in hand
    puts it AWAY (Roblox toggles). `before`: hotbar_band() grabbed before the
    bag was opened, for the changed-slot clue."""
    frame = grab()
    band = hotbar_band(frame)
    if save_crop is not None:
        save_crop(band)
    reads = read_hotbar_words(band, frame.shape[1])
    slot, what = find_hotbar_slot(reads, frame.shape[1], rod, others, enchants,
                                  before, band)
    if slot is None:
        log(f"  hotbar: {what} -- press the rod's number yourself")
        return False
    inp.tap(0x30 + (slot % 10))                    # slot 10 is the 0 key
    log(f"  hotbar: {what} -- pressed {slot % 10}")
    return True


def ensure_rod_held(grab, rod, others, enchants, inp, ready, log, snap=None):
    """Verify the named rod AND held frame before using its number key.
    Returns held/restored/unverified/unknown/failed/cancelled. Unknown sends no
    input. The UI check never opens a bag or toggles an already-held rod.
    `snap(band)`: called with the hotbar strip when no held frame is seen
    (Record measurements: hotbar_*.png, to work out why)."""
    from fischuse import read_hotbar

    if not ready():
        return "cancelled"
    frame = grab()
    hb = read_hotbar(frame)
    slot, what = find_hotbar_slot(hb.reads, frame.shape[1], rod, others, enchants)
    if slot is None:
        log(f"rod check: cannot verify {rod} ({what}); no key pressed")
        return "unknown"
    if hb.held == slot:
        log(f"rod check: {rod} is in hand (slot {slot})")
        return "held"
    if not ready():
        return "cancelled"
    inp.tap(0x30 + slot % 10)
    end = time.monotonic() + .4
    while time.monotonic() < end:
        if not ready():
            return "cancelled"
        time.sleep(.025)
    frame = grab()
    after = read_hotbar(frame)
    after_slot, _ = find_hotbar_slot(after.reads, frame.shape[1], rod, others, enchants)
    if after_slot == slot and after.held == slot:
        log(f"rod check: restored {rod} to hand (slot {slot})")
        return "restored"
    if after.held is None:
        if snap is not None and after.band is not None:
            snap(after.band)
        # No held frame anywhere: the key may have taken the rod OUT of hand
        # (it toggles) or the frame just isn't readable. Live (2026-10-05) the
        # old "failed" pressed again every 5s -- rod in, out, in -- and never
        # cast. The caller casts instead; no reel then means press once more.
        log(f"rod check: pressed {slot} for {rod} but can't see the held frame -- "
            f"casting to find out (no key until then)")
        return "unverified"
    log(f"rod check: {rod} did not return to hand; delaying the next cast")
    return "failed"


def _card_pos(c: dict) -> tuple[float, float]:
    return (c["box"][0] + c["box"][2]) / 2, c["box"][3]


def _moved(before: Screen, after: Screen) -> bool:
    """Did the rod list move between two reads? Rods on both screens are
    compared; none in common (a whole page scrolled) also counts as moved.
    Nothing readable after is NOT movement: better to stop than loop."""
    if not after.cards:
        return False
    old = {c["rod"]: _card_pos(c) for c in before.cards}
    shifts = [abs(x - old[c["rod"]][0]) + abs(y - old[c["rod"]][1])
              for c in after.cards if c["rod"] in old
              for x, y in [_card_pos(c)]]
    if not shifts:
        return True
    return float(np.median(shifts)) > SCROLL_SAME_PX
