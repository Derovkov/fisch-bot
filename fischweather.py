"""Read the bottom-right weather HUD. Hover/OCR runs only between casts.

Templates locate the icons, not world effects. Similar star icons require
tooltip confirmation. Images and tooltip crops stay in memory for this run.
Rules are from ui/weather.json and the official wiki Weather/Totems pages:
protected conditions block totems in the same group; active effects cannot
be triggered again. Local events without verified rules are deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import time

import cv2
import numpy as np

UI = Path(__file__).with_name("ui")
MATCH_MIN = 0.70
NAME_MIN = 0.78
NAME_MARGIN = 0.07
MAX_AGE_S = 5.0
HOVER_S = 0.35
TOOLTIP_CACHE_S = 60.0


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _highpass(a, sigma):
    a = a.astype(np.float32)
    return a - cv2.GaussianBlur(a, (0, 0), sigma)


@dataclass
class Icon:
    box: tuple[int, int, int, int]
    candidates: tuple[str, ...]
    score: float
    name: str | None = None
    fingerprint: tuple = ()
    source: str = "unknown"


@dataclass
class WeatherState:
    names: tuple[str, ...] = ()
    complete: bool = False
    checked_at: float = 0.0
    unknown: int = 0
    reason: str = "not read yet"
    icons: list[Icon] = field(default_factory=list)

    def view(self):
        return {"names": list(self.names), "complete": self.complete,
                "checked_at": self.checked_at, "unknown": self.unknown,
                "reason": self.reason,
                "sources": {i.name: i.source for i in self.icons if i.name}}


class WeatherReader:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.index = {w["name"]: w for w in json.loads(
            (UI / "weather.json").read_text(encoding="utf-8"))["weather"]}
        self.images = {}
        for name, w in self.index.items():
            if w.get("icon"):
                im = cv2.imread(str(UI / w["icon"]), cv2.IMREAD_UNCHANGED)
                if im is not None and im.ndim == 3 and im.shape[2] == 4:
                    self.images[name] = im
        self.state = WeatherState()
        self._templates = {}
        self._tooltips = {}

    def _template(self, name, size):
        key = name, size
        if key not in self._templates:
            alpha = cv2.resize(self.images[name][:, :, 3], (size, size),
                               interpolation=cv2.INTER_AREA)
            self._templates[key] = _highpass(alpha, max(1, size * 3 / 38))
        return self._templates[key]

    def locate(self, frame):
        """Use the season/time anchors to read one aligned icon row.

        No anchors, gaps, unrecognised icons or a row reaching the search
        boundary mean incomplete, never 'clear weather'. Coordinates are
        client pixels. Scale is searched rather than changing window size.
        """
        h, w = frame.shape[:2]
        scale = min(w / 1920, h / 1009)
        scales = sorted(set((max(.3, scale), max(.3, w / 1920), 1.0)))
        sizes = sorted({max(14, round(38 * s) + d) for s in scales
                        for d in (-2, 0, 2) if round(38 * s) + d < min(h, w)})
        xoff, yoff = max(0, w - round(700 * max(scales))), max(0, h - round(220 * max(scales)))
        # Season is the rightmost icon; exclude level/currency text by requiring
        # a matching time icon immediately to its left.
        roi = cv2.cvtColor(frame[yoff:, xoff:], cv2.COLOR_RGB2GRAY)
        anchors = []
        for size in sizes:
            g = _highpass(roi, max(1, size * 3 / 38))
            for name in ("Spring", "Summer", "Autumn", "Winter"):
                r = cv2.matchTemplate(g, self._template(name, size), cv2.TM_CCOEFF_NORMED)
                _, score, _, (x, y) = cv2.minMaxLoc(r)
                if score < NAME_MIN or x + xoff < w * .85:
                    continue
                pitch = max(size + 1, round(size * 40 / 38))
                icons = self._cells(frame, x + xoff, y + yoff, size, pitch)
                if len(icons) >= 3 and icons[1].name in ("Day", "Night"):
                    anchors.append((score + icons[1].score, icons))
        if not anchors:
            return [], False
        _, icons = max(anchors, key=lambda a: a[0])
        # At least a primary weather, time and season, with no unread cells.
        groups = [self.index[i.name]["group"] for i in icons if i.name]
        complete = (len(icons) < 16 and all(i.name for i in icons) and
                    groups.count("time") == 1 and groups.count("season") == 1
                    and "weather" in groups)
        return icons, complete

    def _cells(self, frame, x, y, size, pitch):
        icons = []
        for n in range(16):
            left = x - n * pitch
            if left < 0:
                break
            patch = frame[y:y + size, left:left + size]
            if patch.shape[:2] != (size, size):
                return []
            g = _highpass(cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY), max(1, size * 3 / 38))
            activity = float(np.mean(g > 35))
            scores = []
            for name in self.images:
                # Local +/- 2px search handles rounding at smaller UI sizes.
                pad = max(0, left - 2), max(0, y - 2)
                area = frame[pad[1]:y + size + 2, pad[0]:left + size + 2]
                a = _highpass(cv2.cvtColor(area, cv2.COLOR_RGB2GRAY), max(1, size * 3 / 38))
                r = cv2.matchTemplate(a, self._template(name, size), cv2.TM_CCOEFF_NORMED)
                scores.append((float(cv2.minMaxLoc(r)[1]), name))
            scores.sort(reverse=True)
            best, name = scores[0]
            if n >= 3 and best < MATCH_MIN and activity < .035:
                break
            candidates = tuple(sorted(n for v, n in scores if v >= max(MATCH_MIN, best - NAME_MARGIN)))
            known = name if best >= NAME_MIN and len(candidates) == 1 else None
            fg = patch[g > 35]
            colour = tuple(np.median(fg, axis=0).astype(int)) if len(fg) else ()
            # Cache follows icon identity/colour, not its background or its
            # position (new modifiers shift the whole row).
            # A strong unique shape identifies ordinary weather/time/season.
            # Lighting and 1-2px template scale jitter must not trigger hovers.
            # Shared silhouettes still include colour to distinguish modifiers.
            fingerprint = (frame.shape[:2], candidates, () if known else colour)
            icons.append(Icon((left, y, size, size), candidates, best, known,
                              fingerprint, "icon" if known else "unknown"))
        return icons

    def tooltip_name(self, lines, candidates=()):
        """Match the tooltip's title, not mentions in its description.
        Time tooltips may include a clock (e.g. 'Night 02:34')."""
        aliases = {name: (name,) for name in self.index}
        aliases["Day/Night of the Luminous"] += ("Night of the Luminous", "Day of the Luminous")
        for text, _ in lines[:3]:
            text = re.sub(r"\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b", "", text, flags=re.I).strip()
            norm = _norm(text)
            if not norm:
                continue
            matches = sorted((max(SequenceMatcher(None, norm, _norm(a)).ratio() for a in aa), name)
                             for name, aa in aliases.items())
            score, name = matches[-1]
            runner = matches[-2][0]
            if score >= .88 and score - runner >= .07:
                # Template and text disagree: don't silently trust unrelated text.
                if candidates and name not in candidates:
                    return None
                return name
        return None

    @staticmethod
    def _ready(bot):
        return bot.running and not bot.mouse.dry_run and bot.focus.ready()

    def _cached(self, icon):
        cached = self._tooltips.get(icon.fingerprint)
        if cached or len(icon.fingerprint) != 3:
            return cached
        shape, candidates, colour = icon.fingerprint
        if not colour or not candidates:
            return None
        for key, value in self._tooltips.items():
            if len(key) != 3:
                continue
            old_shape, old_candidates, old_colour = key
            if (shape == old_shape and candidates == old_candidates and len(old_colour) == len(colour)
                    and max(abs(int(a) - int(b)) for a, b in zip(colour, old_colour)) <= 8):
                return value
        return None

    def refresh(self, bot, confirm=False):
        """Called only at a cast boundary. Stop/focus are checked before input,
        capture and OCR; stale tooltip caches cannot prove effect absence."""
        from fischequip import WinInput
        from fischocr import ocr_lines

        now = self.clock()
        self.state = WeatherState(checked_at=now, reason="HUD unreadable")
        if not self._ready(bot):
            self.state.reason = "paused or Roblox not focused"
            return self.state
        bot.mouse.release()
        # Keep the cursor in place on unchanged reads; recenter only when
        # hovering. Repeated camera nudges are unnecessary between casts.
        frame = bot.grabber.grab()
        icons, _ = self.locate(frame)
        hovered = False
        try:
            for icon in icons:
                cached = self._cached(icon)
                if not confirm and cached and 0 <= now - cached[0] < TOOLTIP_CACHE_S:
                    icon.name, icon.source = cached[1], cached[2]
                    self._tooltips[icon.fingerprint] = cached
                    continue
                if not confirm and cached and icon.name:
                    # A currently strong, unique template doesn't need a hover
                    # merely because an old tooltip expired. Ambiguous stars do.
                    continue
                if not self._ready(bot):
                    self.state.reason = "paused or Roblox not focused"
                    return self.state
                x, y, size, _ = icon.box
                hovered = True
                WinInput().move(bot.rect.left + x + size // 2, bot.rect.top + y + size // 2)
                # Interruptible settle, including focus checks before a new grab.
                until = time.monotonic() + HOVER_S
                while time.monotonic() < until:
                    if not self._ready(bot):
                        return self.state
                    time.sleep(.025)
                after = bot.grabber.grab()
                if after.shape != frame.shape:
                    self.state.reason = "window size changed; rechecking next cast"
                    return self.state
                # Only changed pixels near/above the hovered icon can be its
                # tooltip. World labels elsewhere are not candidate titles.
                x0, x1 = max(0, x - 420), min(frame.shape[1], x + size + 100)
                y0, y1 = max(0, y - 180), min(frame.shape[0], y + size + 20)
                before_crop, crop = frame[y0:y1, x0:x1], after[y0:y1, x0:x1]
                changed = np.max(np.abs(crop.astype(np.int16) - before_crop.astype(np.int16)), axis=2) > 35
                lines = ocr_lines(crop, scale=2.0) if np.mean(changed) > .01 else []
                # Require the OCR line itself to overlap changed pixels; do not
                # accept an old label just because a fish/camera moved nearby.
                fresh = []
                for text, (lx, ly, lw, lh) in lines:
                    region = changed[max(0, int(ly)):int(ly + lh), max(0, int(lx)):int(lx + lw)]
                    if region.size and np.mean(region) > .12:
                        fresh.append((text, (lx, ly, lw, lh)))
                name = self.tooltip_name(fresh, icon.candidates)
                if name:
                    icon.name, icon.source = name, "tooltip"
                elif icon.name is None:
                    icon.source = "unknown"
                self._tooltips[icon.fingerprint] = (now, icon.name, icon.source)
                if self._ready(bot):
                    bot.recentre()
                    time.sleep(.05)
                    frame = bot.grabber.grab()
        finally:
            if hovered and bot.running and bot.focus.ready():
                bot.recentre()
        names = tuple(i.name for i in icons if i.name)
        groups = [self.index[n]["group"] for n in names]
        complete = (3 <= len(icons) < 16 and all(i.name for i in icons)
                    and groups.count("time") == 1 and groups.count("season") == 1
                    and "weather" in groups)
        self.state = WeatherState(names, complete, self.clock(),
                                  sum(i.name is None for i in icons),
                                  "" if complete else "HUD hidden, incomplete or unknown icons", icons)
        # A briefly hidden HUD must not erase the cache and cause another
        # full hover sweep at the next cast. Retain recent identities in memory.
        current = {i.fingerprint for i in icons}
        self._tooltips = {k: v for k, v in self._tooltips.items()
                          if k in current or 0 <= now - v[0] < TOOLTIP_CACHE_S}
        return self.state

    def block_reason(self, totem, state=None):
        state = state or self.state
        target = totem.get("weather")
        if (not 0 <= self.clock() - state.checked_at <= MAX_AGE_S or not state.complete
                or any(n not in self.index for n in state.names)):
            return "waiting for a complete weather reading"
        if target in state.names:
            return f"{target} is already active"
        if totem.get("name") == "Sundial Totem":
            return None  # Time is a separate group; existing interval guards apply.
        info = self.index.get(target)
        if not info:
            return "local event/location rules are not verified yet"
        group = info["group"]
        protected = [n for n in state.names if self.index[n]["group"] == group
                     and self.index[n].get("skippable") is False]
        if protected:
            return f"{', '.join(protected)} protects the {group} group"
        return None
