"""
Desktop UI for the Fisch bot -- an HTML/CSS front end (ui/index.html) in a native
window via pywebview (Edge WebView2 on Windows).

    python fischui.py

The page talks to the Api class below: it polls get_state() a few times a second
and calls start()/stop(). Each Start is a fresh run: it re-calibrates for the
current area (fischcalib.py) and writes only to its own temporary folder, which is
deleted when the run stops (fischsession.py) unless "Keep logs" is on.

F9 stops the bot from any window (needs the `keyboard` package).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import webview

from fischbot import FischBot, MacroConfig, NoRobloxWindow, create_bot
from fischcontrol import ControlConfig
from fischrods import DEFAULT_ROD, ENCHANTS, ROD_DATA, SPECIAL, get_rod
from fischsession import Session

UI_FILE = Path(__file__).with_name("ui") / "index.html"
SETTINGS_FILE = Path(__file__).with_name("fischbot_settings.json")
MAX_LOG_LINES = 1000

HELP = {
    "rod": "The rod you have equipped -- pick it on the Rods page, where you can also "
           "set its enchants. Some rods run their own minigame during the reel; the "
           "bot plays those itself using that rod's profile.",
    "fish": "How many casts to make before stopping. 0 keeps fishing until you press "
            "Stop.",
    "focus": "Keep Roblox in front: Roblox stays the active window so the bot can see "
             "and click it. Runs unattended, but you can't use the PC meanwhile. "
             "Pause when I switch: you can use other windows; the bot pauses while "
             "Roblox isn't in front, and a reel in progress is lost.",
    "debug": "Adds a log line about every 0.3s while reeling: slider position, fish "
             "position, the gap between them, and whether the button is held.",
    "trace": "Records what the bot sees, to troubleshoot an area or a rod where it "
             "struggles: numbers 10 times a second, plus small snapshots of just the "
             "reel bar (never the whole screen) when the bar can't be read. Deleted "
             "when the run stops unless Keep logs is on.",
    "keep": "Copies this run's logs to the saved_logs folder before the temporary "
            "folder is deleted. Leave off to keep your storage clean.",
    "lookahead": "How many seconds ahead the bot predicts where the fish and slider "
                 "will be. Raise it if the slider keeps overshooting back and forth; "
                 "lower it if the slider lags behind the fish. Default 0.5.",
    "deadband": "How far the fish may drift from the slider's centre, as a fraction "
                "of the slider's width, before the bot reacts. Higher means fewer "
                "clicks but looser tracking. Default 0.06.",
    "bite": "Seconds to wait for a bite after casting before giving up and recasting. "
            "Harder areas can take 15s or more. Default 30.",
}

DEFAULTS = {"rod": DEFAULT_ROD, "max_fish": 20, "focus": "pin",
            "debug": False, "trace": False, "keep": False,
            "lookahead": 0.5, "deadband": 0.06, "bite": 30,
            # Rods page: fill these in with "Scan from game", or with the ✓ / ✎
            # buttons on each rod card.
            "rod_enchants": {}, "owned": [], "favs": [], "rod_layout": "carousel"}


class Api:
    """Called from the page's JavaScript (window.pywebview.api.*)."""

    def __init__(self):
        self._bot: FischBot | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._logs: list[tuple[int, str]] = []
        self._seq = 0
        self._runs: list[dict] = []          # this app session only, in memory
        self._run: dict | None = None
        self._calibrated = False

    # --- page -> python ----------------------------------------------------------
    def get_meta(self) -> dict:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        rods = [dict(r, extra=r["name"] in SPECIAL and SPECIAL[r["name"]].has_extra)
                for r in ROD_DATA]
        return {"rods": rods, "enchants": ENCHANTS, "defaults": DEFAULTS,
                "saved": saved, "help": HELP}

    def save_settings(self, settings_json: str) -> None:
        """Settings persist on purpose (they are not run data): a few hundred
        bytes next to this file. The webview runs in private mode, so the page's
        own localStorage would be wiped on every launch."""
        try:
            s = json.loads(settings_json)
            SETTINGS_FILE.write_text(json.dumps({k: s[k] for k in DEFAULTS if k in s},
                                                indent=1), encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass

    def start(self, settings_json: str) -> dict:
        # Sent as a JSON string: a JS object passed straight through pywebview
        # arrived without its keys ("invalid setting: 'max_fish'").
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "already running"}
        try:
            s = json.loads(settings_json)
            cfg = MacroConfig(max_fish=int(s["max_fish"]), focus_mode=s["focus"],
                              bite_timeout_s=float(s["bite"]),
                              debug=bool(s["debug"]), trace=bool(s["trace"]))
            ccfg = ControlConfig(lookahead_s=float(s["lookahead"]),
                                 deadband_frac=float(s["deadband"]))
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"invalid setting: {exc}"}
        self._bot = None
        self._calibrated = False
        self._run = {"n": len(self._runs) + 1, "rod": s["rod"], "started": time.time(),
                     "max_fish": cfg.max_fish, "caught": 0, "lost": 0, "casts": 0,
                     "ended": None}
        self._runs.append(self._run)
        enchants = list((s.get("rod_enchants") or {}).get(s["rod"], []))
        self._worker = threading.Thread(
            target=self._work,
            args=(cfg, ccfg, get_rod(s["rod"]), bool(s["keep"]), enchants), daemon=True)
        self._worker.start()
        return {"ok": True}

    def scan_rods(self) -> dict:
        """Read the rod screen currently open in Roblox (Equipment Bag -> Fishing
        Rods). Brings Roblox to the front, grabs one frame, OCRs it. Nothing is
        changed until the page applies the results the user ticks."""
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "stop the bot before scanning"}
        try:
            from fastcap import FastGrabber, find_roblox_window, focus_window
            from fischscan import parse_rod_screen
        except Exception as exc:                 # winrt OCR bindings missing
            return {"ok": False, "error": f"scanner unavailable: {exc}"}
        win = find_roblox_window()
        if win is None:
            return {"ok": False, "error": "No Roblox window found."}
        hwnd = win[0]
        focus_window(hwnd)
        time.sleep(0.6)                          # let Roblox redraw in front
        win = find_roblox_window() or win
        g = FastGrabber(win[1])
        try:
            frame = g.grab()
        finally:
            g.close()
        try:
            cards = parse_rod_screen(frame)
        except Exception as exc:
            return {"ok": False, "error": f"text recognition failed: {exc!r}"}
        self._log(f"[{datetime.now():%H:%M:%S}] rod scan: {len(cards)} rod(s) read")
        if not cards:
            return {"ok": False, "error": "No rods found. Open Equipment Bag -> "
                                          "Fishing Rods in Roblox, then scan again."}
        return {"ok": True, "cards": [{k: c[k] for k in ("rod", "enchants", "equipped")}
                                      for c in cards]}

    def stop(self) -> None:
        if self._bot is not None and self._bot.running:
            self._log("stopping...")
            self._bot.stop()

    def get_state(self, since: int = 0) -> dict:
        b = self._bot
        running = bool(self._worker and self._worker.is_alive())
        with self._lock:
            logs = [l for l in self._logs if l[0] > since]
        if b is not None and self._run is not None:
            self._run.update(caught=b.caught, lost=b.lost, casts=b.cycles)
        return {
            "running": running,
            "state": (b.state if b is not None and running
                      else "starting" if running else "idle"),
            "caught": b.caught if b else 0,
            "lost": b.lost if b else 0,
            "casts": b.cycles if b else 0,
            "live": dict(b.live) if b else None,
            "history": list(b.reel_history) if b else [],
            "max_fish": self._run["max_fish"] if self._run else 0,
            "started": self._run["started"] if self._run else None,
            "calibrated": self._calibrated,
            "runs": [dict(r) for r in reversed(self._runs)],
            "logs": logs,
            "now": time.time(),
        }

    # --- worker ------------------------------------------------------------------
    def _log(self, line: str) -> None:
        if "calibration:" in line and ("OK" in line or "adopted" in line):
            self._calibrated = True
        with self._lock:
            self._seq += 1
            self._logs.append((self._seq, line))
            del self._logs[:-MAX_LOG_LINES]

    def _work(self, cfg, ccfg, rod, keep, enchants) -> None:
        session = Session(keep_logs=keep)
        if session.swept:
            self._log(f"removed {session.swept} leftover temp folder(s) from an "
                      f"earlier run")
        try:
            self._bot = create_bot(cfg, ccfg, rod, session, on_log=self._log,
                                   enchants=enchants)
            self._bot.run()
        except NoRobloxWindow as exc:
            self._log(f"error: {exc}")
        except Exception as exc:                      # surface, never hang the UI
            self._log(f"error: {exc!r}")
        finally:
            if self._bot is not None:
                self._bot.mouse.release()
                self._bot.mouse.restore_cursor()
                self._bot.grabber.close()
                self._run.update(caught=self._bot.caught, lost=self._bot.lost,
                                 casts=self._bot.cycles)
            session.close()
            self._run["ended"] = time.time()
            self._log(f"[{datetime.now():%H:%M:%S}] temporary files deleted"
                      + (f"; logs kept in {session.kept_to}" if session.kept_to else ""))


def main() -> None:
    api = Api()
    try:
        import keyboard
        keyboard.add_hotkey("f9", api.stop)
    except Exception as exc:
        api._log(f"F9 hotkey unavailable ({exc}); use the Stop button")
    win = webview.create_window("Fisch bot", url=str(UI_FILE), js_api=api,
                                width=1280, height=840, min_size=(1080, 720),
                                background_color="#0c0d10")

    def on_closing():
        api.stop()
        if api._worker and api._worker.is_alive():
            api._worker.join(timeout=5)

    win.events.closing += on_closing
    webview.start()


if __name__ == "__main__":
    main()
