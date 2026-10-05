"""
Desktop UI for the Fisch bot -- an HTML/CSS front end (ui/index.html) in a native
window via pywebview (Edge WebView2 on Windows).

    python fischui.py

The page talks to the Api class below: it polls get_state() a few times a second
and calls start()/stop(). Each Start is a fresh run: it re-calibrates for the
current area (fischcalib.py) and writes only to its own temporary folder, which is
deleted when the run stops (fischsession.py) unless "Keep logs" is on.

Configurable global shortcuts: F7 Start, F9 Stop, F6 next setup by default.
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
from fischconfig import ConfigurationStore, configuration, rotation_settings
from fischrods import DEFAULT_ROD, ENCHANTS, ROD_DATA, SPECIAL, get_rod
from fischsession import Session
from fischuse import Useables, load_general, save_general
from fischskins import SkinBook, clean_skins
from fischkeys import Hotkeys, saved_hotkeys, clean_hotkeys
from fischlullaby import MODES as LULLABY_MODES, LullabyBuffs

UI_FILE = Path(__file__).with_name("ui") / "index.html"
SETTINGS_FILE = Path(__file__).with_name("fischbot_settings.json")
PROFILES_FILE = Path(__file__).with_name("fischbot_profiles.json")
MAX_LOG_LINES = 1000

HELP = {
    "hotkeys": "Global shortcuts while the app is open. Type a key name or a "
               "combination such as ctrl+alt+r, then Save hotkeys to apply it "
               "immediately. Each action needs a different shortcut. Start uses "
               "your latest saved settings; Stop also cancels a rod scan; Next "
               "saved setup waits until the current cast finishes. These keys "
               "are kept in the general config, separate from rod setups.",
    "profiles": "Save named setups for rods, enchants, run goals, control and logging. "
                "Switch while fishing to apply after the current cast finishes. "
                "Press F6 in Roblox to cycle through all your setups without "
                "switching windows. With 'Equip rod in game' on, a setup with a "
                "different rod makes the bot open the Equipment Bag (N), search "
                "for that rod, equip it and close the bag -- between casts, while "
                "the rod is reeled in.",
    "rotations": "A rotation fishes a set time with each rod, then switches to the "
                 "next one, and starts over after the last. The switch waits for the "
                 "current cast, then the bot equips the next rod through the Equipment "
                 "Bag (N), so 'Equip rod in game' must be on. A rod that can't be "
                 "equipped is skipped. Picking a setup, F6, or Stop rotating ends it. "
                 "Time counts from when each rod is equipped, including waits.",
    "fast_cast": "Shorter waits between reels. The bot casts again about a second "
                 "after a catch instead of waiting for the catch message to fade, "
                 "starts shaking sooner after the cast, and reads the quest tracker "
                 "and weather every 3rd cast instead of every cast (the weather is "
                 "still read whenever a totem is due). Catches are still confirmed. "
                 "Turn it off if casts get cancelled or reels are missed at the start.",
    "default_skin": "Rod skins change the reel bar, and skinned bars are much harder "
                    "to read. With this on, the bot opens the Equipment Bag once per rod "
                    "per run, presses the rod's skins (pen) button, equips its Default "
                    "skin and presses Back. Your skin stays on Default afterwards -- "
                    "pick it again in the bag when you're done.",
    "auto_equip": "When a switch changes the rod, the bot equips it in Roblox for "
                  "you (Equipment Bag, N). If it can't, it keeps the current setup "
                  "and says why in the log. Off: match the rod in Roblox yourself.",
    "track_quests": "Reads the quest tracker on the left of the Roblox screen at the start "
                    "and after every catch: logs progress like 0/1 -> 1/1 and finished "
                    "quests, and, for objectives that need a mutation, which of your rods "
                    "(or which weather, totem or enchant) can give it. Only quest text "
                    "is logged.",
    "scan": "Search each rod: types every rod name into the Equipment Bag's search "
            "and reads the result -- a few minutes, but finds every rod you own. "
            "Scroll: scrolls through your rods with the mouse wheel -- under a "
            "minute, but a rod half off the screen can be missed. Both open and "
            "close the bag themselves. Don't touch the mouse or keyboard while it "
            "runs; Stop or F9 cancels.",
    "lullaby": "Grinds the Lullaby's buffs while you fish with it. Each metronome "
               "hit adds 2.5s of the current mode's buff (the bot already times its "
               "clicks to the metronome). Between casts the bot opens the Equipment "
               "Bag (N), finds the Lullaby's card and presses the mode button on its "
               "right side for the buff you want, then closes the bag. With more than "
               "one buff in the list it switches to the next when the time is up, and "
               "starts over after the last. Only works while the Lullaby is your rod. "
               "Kept in the general config: switching setups never changes it.",
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
             "reel bar (never the whole screen) when the bar can't be read, and the "
             "first 8 seconds of the first two reels as a short clip of just the bar "
             "(a few MB) for working out rod skins. Deleted when the run stops unless "
             "Keep logs is on.",
    "keep": "Copies this run's logs to the saved_logs folder before the temporary "
            "folder is deleted. Leave off to keep your storage clean.",
    "lookahead": "How many seconds ahead the bot predicts where the fish and slider "
                 "will be. Raise it if the slider keeps overshooting back and forth; "
                 "lower it if the slider lags behind the fish. Default 0.5.",
    "deadband": "How far the fish may drift from the slider's centre, as a fraction "
                "of the slider's width, before the bot reacts. Higher means fewer "
                "clicks but looser tracking. Default 0.06.",
    "skins": "Rod skins change how the reel bar looks -- even the track's length and "
             "how the progress bar fills. The bot fingerprints each reel's bar and "
             "saves every skin it reads well, with what it learned (slider width per "
             "rod, track length). Next time it recognises the skin and starts from "
             "that. Rename skins so the log is easy to read; Forget makes the bot "
             "learn it again.",
    "useables": "Totems and baits the bot may use for you, between casts. Kept apart "
                "from your saved setups: switching setups (F6) never changes it. "
                "Per item: 'Max per run' caps how many the bot uses in one run (0 = no "
                "cap); 'Keep at least' stops using it when that many are left, and it "
                "resumes by itself once you have more. Totems must be in your hotbar.",
    "useables_totems": "When: At start -- once when the run starts. Every -- every N "
                       "minutes. For quests -- when a tracked quest needs a mutation the "
                       "totem's weather gives (the Mutation Totem: any mutation), at most "
                       "every N minutes. The bot presses the totem's hotbar number, checks "
                       "it is in hand, clicks, and takes the rod back with T. Between casts "
                       "it reads the weather icons and hovers for tooltip names. Active "
                       "effects, protected weather groups and incomplete readings defer "
                       "totems. Local event/location rules that are not verified also defer.",
    "useables_baits": "Your bait order. The bot keeps the first bait that is on and within "
                      "its limits equipped (reading 'Current Bait: ... [xN]' above the "
                      "hotbar) and switches to the next one in the Equipment Bag's Baits tab "
                      "when it runs down. One cast uses one bait.",
    "bite": "Seconds to wait for a bite after casting before giving up and recasting. "
            "Harder areas can take 15s or more. Default 30.",
}

DEFAULTS = {"rod": DEFAULT_ROD, "max_fish": 20, "focus": "pin",
            "debug": False, "trace": False, "keep": False,
            "lookahead": 0.5, "deadband": 0.06, "bite": 30, "fast_cast": False,
            # Rods page: fill these in with "Scan from game", or with the ✓ / ✎
            # buttons on each rod card.
            "rod_enchants": {}, "owned": [], "favs": [], "rod_layout": "carousel",
            "active_profile": "", "active_rotation": "", "auto_equip": True,
            "default_skin": False,
            "track_quests": True}


class SearchCancelled(RuntimeError):
    pass


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
        self._profiles = ConfigurationStore(PROFILES_FILE, DEFAULTS,
                                             {r['name'] for r in ROD_DATA})
        self._pending_config = None
        self._active_config = None
        self._rotation = None        # running rod rotation: steps, index, since
        self._stop_requested = threading.Event()
        self._scan: dict = {"busy": False}       # rod scan progress (scan_rods)
        self._search: dict = {"busy": False}
        self._start_lock = threading.Lock()
        self._hotkeys = None
        self._hotkey_error = ""

    # --- page -> python ----------------------------------------------------------
    def get_meta(self) -> dict:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        rods = [dict(r, extra=r["name"] in SPECIAL and SPECIAL[r["name"]].has_extra)
                for r in ROD_DATA]
        keys = saved_hotkeys(load_general())
        help_text = {k: v.replace("F6", keys["switch"].upper()).replace("F9", keys["stop"].upper())
                     for k, v in HELP.items()}
        return {"rods": rods, "enchants": ENCHANTS, "defaults": DEFAULTS,
                "saved": saved, "help": help_text, "profiles": self._profiles.list(),
                "rotations": self._profiles.list_rotations(),
                "lullaby_modes": LULLABY_MODES,
                "hotkeys": keys, "help_base": HELP,
                "links": {"discord": DISCORD_URL, "repo": REPO_URL,
                          "issues": REPO_URL + "/issues", "releases": REPO_URL + "/releases"}}

    def save_profile(self, name: str, settings_json: str, profile_id: str = '') -> dict:
        try:
            row = self._profiles.save(name, json.loads(settings_json), profile_id)
            return {"ok": True, "profile": row, "profiles": self._profiles.list()}
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def delete_profile(self, profile_id: str) -> dict:
        try:
            self._profiles.delete(profile_id)
            return {"ok": True, "profiles": self._profiles.list()}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # --- rod rotations ------------------------------------------------------------
    def save_rotation(self, name: str, data_json: str, rotation_id: str = '') -> dict:
        try:
            row = self._profiles.save_rotation(name, json.loads(data_json), rotation_id)
            return {"ok": True, "rotation": row, "rotations": self._profiles.list_rotations()}
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def delete_rotation(self, rotation_id: str) -> dict:
        try:
            self._profiles.delete_rotation(rotation_id)
            return {"ok": True, "rotations": self._profiles.list_rotations()}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def _rotation_request(self, rotation_id: str, extra: dict | None = None):
        """(request for the rotation's first rod, fresh rotation state), or an
        error string. Rotations switch rods in the game, so they need
        'Equip rod in game'."""
        rot = next((r for r in self._profiles.list_rotations() if r['id'] == rotation_id), None)
        if rot is None:
            return "That rotation no longer exists."
        if not self._auto_equip():
            return "Turn on 'Equip rod in game' to use a rotation -- it switches rods for you."
        settings = [rotation_settings(rot, i) for i in range(len(rot['steps']))]
        request = self._config_request(dict(extra or {}, **settings[0], active_profile=''))
        fresh = dict(id=rot['id'], name=rot['name'], steps=rot['steps'], settings=settings,
                     index=0, since=0.0, next=None)
        request.update(id='', name=f"{rot['name']} · {rot['steps'][0]['rod']}",
                       origin='rotation', rotation=dict(id=rot['id'], step=0))
        return request, fresh

    def queue_rotation(self, rotation_id: str) -> dict:
        """Switch to a rotation during a run: its first rod after this cast."""
        made = self._rotation_request(rotation_id)
        if isinstance(made, str):
            return {"ok": False, "error": made}
        request, fresh = made
        request['rotation_new'] = fresh
        with self._lock:
            if not (self._worker and self._worker.is_alive()):
                return {"ok": False, "error": "The run has ended. This rotation is ready for your next Start."}
            self._pending_config = request
        self._note(f"rotation queued: {fresh['name']} -- starts with {fresh['steps'][0]['rod']} "
                   f"after the current cast")
        return {"ok": True}

    def end_rotation(self) -> None:
        """Stay on the current rod and stop rotating."""
        with self._lock:
            rot, self._rotation = self._rotation, None
            if self._pending_config and self._pending_config.get('origin') == 'rotation':
                self._pending_config = None
        if rot is not None:
            self._note(f"rotation {rot['name']} stopped -- staying on the current rod")

    def _rotation_tick(self) -> None:
        """Cast boundary: when the current rod's time is up, queue the next rod."""
        with self._lock:
            rot = self._rotation
            if rot is None or self._pending_config is not None:
                return
            cur = rot['steps'][rot['index']]
            retry = rot['next'] is not None
            if not retry and time.time() - rot['since'] < cur['minutes'] * 60:
                return
            nxt = rot['next'] if retry else (rot['index'] + 1) % len(rot['steps'])
            step = rot['steps'][nxt]
            self._pending_config = dict(settings=rot['settings'][nxt], id='',
                                        name=f"{rot['name']} · {step['rod']}", origin='rotation',
                                        rotation=dict(id=rot['id'], step=nxt))
        if not retry:
            self._note(f"rotation {rot['name']}: {cur['minutes']:g} min with {cur['rod']} done "
                       f"-- switching to {step['rod']}")

    def _rotation_failed(self, step: int) -> None:
        """A rotation rod could not be equipped: try the one after it at the next
        cast; if none can be, stay on the current rod for another full turn."""
        with self._lock:
            rot = self._rotation
            if rot is None:
                return
            nxt = (step + 1) % len(rot['steps'])
            if nxt == rot['index']:
                rot.update(next=None, since=time.time())
                cur = rot['steps'][rot['index']]
            else:
                rot['next'] = nxt
                cur = None
        if cur is not None:
            self._note(f"rotation {rot['name']}: no other rod could be equipped -- "
                       f"staying on {cur['rod']} for another {cur['minutes']:g} min")

    def _rotation_view(self):
        rot = self._rotation
        if rot is None:
            return None
        cur = rot['steps'][rot['index']]
        nxt = rot['steps'][(rot['index'] + 1) % len(rot['steps'])]
        return {"id": rot['id'], "name": rot['name'], "index": rot['index'],
                "steps": [{"rod": st['rod'], "minutes": st['minutes']} for st in rot['steps']],
                "left_s": max(0.0, cur['minutes'] * 60 - (time.time() - rot['since'])),
                "next_rod": nxt['rod']}

    def _on_cycle_boundary(self) -> None:
        self._rotation_tick()
        self._apply_pending()

    def _config_request(self, s):
        settings = configuration(s, DEFAULTS, self._profiles.rods)
        profile_id = s.get('active_profile', '')
        row = next((p for p in self._profiles.list() if p['id'] == profile_id), None)
        name = row['name'] if row else 'Custom setup'
        if row and row['settings'] != settings:
            name += ' · edited'
        return dict(settings=settings, id=profile_id, name=name)

    @staticmethod
    def _configs(s):
        return (MacroConfig(max_fish=s['max_fish'], focus_mode=s['focus'],
                            bite_timeout_s=s['bite'], debug=s['debug'], trace=s['trace'],
                            fast_cast=s['fast_cast']),
                ControlConfig(lookahead_s=s['lookahead'], deadband_frac=s['deadband']))

    def queue_configuration(self, settings_json: str, origin: str = 'ui') -> dict:
        try:
            request = self._config_request(json.loads(settings_json))
            request['origin'] = origin
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            if not (self._worker and self._worker.is_alive()):
                return {"ok": False, "error": "The run has ended. This setup is ready for your next Start."}
            self._pending_config = request
        self._note(f"configuration queued: {request['name']} -- after the current cast")
        return {"ok": True}

    def _auto_equip(self) -> bool:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        return bool(saved.get("auto_equip", DEFAULTS["auto_equip"]))

    def cycle_configuration(self) -> None:
        """F6: queue the next saved setup without taking Roblox focus. All
        setups, any rod: with auto-equip on, a different rod is equipped in the
        game between casts. (It used to cycle only the current rod's setups --
        with one setup per rod, F6 did nothing.)"""
        with self._lock:
            active, pending = self._active_config, self._pending_config
        if not (active and self._worker and self._worker.is_alive()):
            self._log("Switch shortcut: start the bot first to switch setups during a run")
            return
        rows = self._profiles.list()
        if not rows:
            self._log("Switch shortcut: no saved setups yet -- save one on the dashboard")
            return
        if len(rows) == 1 and rows[0]['id'] == active['id'] and not pending:
            self._note(f"Switch shortcut: only one saved setup ({rows[0]['name']}) -- save another "
                      f"to switch")
            return
        current_id = pending['id'] if pending else active['id']
        current = next((i for i, row in enumerate(rows) if row['id'] == current_id), -1)
        row = rows[(current + 1) % len(rows)]
        s = dict(row['settings'], active_profile=row['id'])
        self.queue_configuration(json.dumps(s), origin='hotkey')

    def cancel_configuration(self) -> None:
        with self._lock:
            self._pending_config = None

    def _apply_pending(self) -> None:
        with self._lock:
            request, self._pending_config = self._pending_config, None
        if request is None or self._bot is None or not self._bot.running:
            return
        s = request['settings']
        step = (request.get('rotation') or {}).get('step')
        if s['rod'] != self._bot.rod.name:
            # Between casts (the rod is reeled in): equip the setup's rod in the
            # game first. If that fails, stay on the current setup -- tuning for
            # a rod that is not in your hands would be worse than no switch.
            # A rotation only makes sense with the rod really switched.
            equip = self._auto_equip()
            why = ("" if equip and self._bot.equip_rod(s['rod'])
                   else "could not be equipped" if equip
                   else "needs 'Equip rod in game' on" if step is not None else "")
            if why:
                self._note(f"configuration NOT applied: {request['name']} -- "
                          f"{s['rod']} {why}; staying on {self._bot.rod.name}")
                if step is not None and 'rotation_new' not in request:
                    self._rotation_failed(step)
                return
        try:
            cfg, ccfg = self._configs(s)
            self._bot.apply_configuration(cfg, ccfg, get_rod(s['rod']),
                                           s['rod_enchants'][s['rod']], s['keep'])
        except Exception as exc:
            self._note(f"configuration switch failed: {exc}")
            return
        ended = None
        with self._lock:
            self._active_config = request
            if 'rotation_new' in request:
                self._rotation = dict(request.pop('rotation_new'), since=time.time())
            elif step is not None and self._rotation and self._rotation['id'] == request['rotation']['id']:
                self._rotation.update(index=step, since=time.time(), next=None)
            elif step is None:
                ended, self._rotation = self._rotation, None   # a setup or F6 ends a rotation
        self._run.update(rod=s['rod'], max_fish=s['max_fish'])
        self._note(f"configuration applied: {request['name']} | rod: {s['rod']}")
        if ended is not None:
            self._note(f"rotation {ended['name']} ended by the switch")

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
        with self._start_lock:
            return self._start(settings_json)

    def start_saved(self) -> None:
        """Global Start uses the most recently saved UI settings."""
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        result = self.start(json.dumps(dict(DEFAULTS, **(saved if isinstance(saved, dict) else {}))))
        if not result["ok"]:
            self._log("Start shortcut: " + result["error"])

    def _start(self, settings_json: str) -> dict:
        # Sent as a JSON string: a JS object passed straight through pywebview
        # arrived without its keys ("invalid setting: 'max_fish'").
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "already running"}
        if self._scan.get("busy") or self._search.get("busy"):
            return {"ok": False, "error": "finish or cancel the rod scan / quest read first"}
        try:
            raw = json.loads(settings_json)
            fresh = None
            if raw.get('active_rotation'):
                made = self._rotation_request(raw['active_rotation'], raw)
                if isinstance(made, str):
                    return {"ok": False, "error": made}
                request, fresh = made
            else:
                request = self._config_request(raw)
            s = request['settings']
            cfg, ccfg = self._configs(s)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            return {"ok": False, "error": f"invalid setting: {exc}"}
        self._bot = None
        self._stop_requested.clear()
        with self._lock:
            self._active_config = request
            self._pending_config = None
            self._rotation = dict(fresh, since=time.time()) if fresh else None
        self._calibrated = False
        self._run = {"n": len(self._runs) + 1, "rod": s["rod"], "started": time.time(),
                     "max_fish": cfg.max_fish, "caught": 0, "lost": 0, "casts": 0,
                     "ended": None}
        self._runs.append(self._run)
        enchants = list((s.get("rod_enchants") or {}).get(s["rod"], []))
        extras = {"track_quests": bool(raw.get("track_quests", DEFAULTS["track_quests"])),
                  "default_skin": bool(raw.get("default_skin", DEFAULTS["default_skin"])),
                  "owned": [r for r in raw.get("owned", []) if isinstance(r, str)]}
        self._worker = threading.Thread(
            target=self._work,
            args=(cfg, ccfg, get_rod(s["rod"]), bool(s["keep"]), enchants, extras),
            daemon=True)
        self._worker.start()
        return {"ok": True}

    def _begin_search(self, kind):
        with self._start_lock:
            if self._worker and self._worker.is_alive():
                return {"ok": False, "error": "stop the bot before searching"}
            if self._search.get("busy") or self._scan.get("busy"):
                return {"ok": False, "error": "finish or cancel the current search first"}
            self._stop_requested.clear()
            self._search = {"busy": True, "kind": kind, "cancelling": False}
        return None

    def _search_check(self, wait=0):
        if self._stop_requested.wait(wait):
            raise SearchCancelled()

    def _end_search(self):
        with self._start_lock:
            self._search = {"busy": False}

    def scan_rods(self, mode: str = "scroll") -> dict:
        error = self._begin_search("rods")
        if error:
            return error
        try:
            return self._scan_rods(mode)
        except SearchCancelled:
            return {"ok": False, "cancelled": True, "error": "Rod search cancelled."}
        finally:
            self._scan = {"busy": False}
            self._end_search()

    def _scan_rods(self, mode: str) -> dict:
        """Find the player's rods and enchants in Roblox's Equipment Bag. Opens
        it with N, reads it, closes it again (fischequip.py).

        mode "search": types every known rod name into the bag's search -- slow
        (a few minutes), but every owned rod is read on its own clean card.
        mode "scroll": scrolls the rod list with the mouse wheel -- fast, but a
        rod caught half off-screen can be missed.

        Blocks until done; the page polls scan_progress() meanwhile. Stop / F9
        cancels. Nothing is changed until the page applies what the user ticks."""
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "stop the bot before scanning"}
        if self._scan.get("busy"):
            return {"ok": False, "error": "a scan is already running"}
        try:
            from fastcap import FastGrabber, find_roblox_window, focus_window
            from fischequip import EquipmentMenu, MenuError
            from fischrods import rod_names
        except Exception as exc:                 # winrt OCR bindings missing
            return {"ok": False, "error": f"scanner unavailable: {exc}"}
        win = find_roblox_window()
        if win is None:
            return {"ok": False, "error": "No Roblox window found."}
        hwnd = win[0]
        self._scan = {"busy": True, "mode": mode, "done": 0, "total": 0, "found": 0}
        self._search_check()
        focus_window(hwnd)
        self._search_check(0.6)                  # let Roblox redraw in front
        win = find_roblox_window() or win
        g = FastGrabber(win[1])
        t0 = time.time()

        def progress(done, total, found):
            self._scan.update(done=done, total=total, found=found)

        try:
            with EquipmentMenu(g.grab, win[1], self._log,
                               cancelled=self._stop_requested.is_set) as menu:
                if mode == "search":
                    found = menu.scan_by_search(rod_names(), progress)
                else:
                    found = menu.scan_by_scroll(progress)
        except MenuError as exc:
            return {"ok": False, "cancelled": self._stop_requested.is_set(),
                    "error": f"Rod scan stopped: {exc}."}
        except Exception as exc:
            return {"ok": False, "error": f"Rod scan failed: {exc!r}"}
        finally:
            g.close()
            self._scan = {"busy": False}
        cancelled = self._stop_requested.is_set()
        self._log(f"[{datetime.now():%H:%M:%S}] rod scan ({mode}): {len(found)} rod(s) "
                  f"read in {time.time() - t0:.0f}s" + (" -- cancelled" if cancelled else ""))
        if not found:
            return {"ok": False, "cancelled": cancelled, "error": "No rods were read." + (
                " Scan cancelled." if cancelled else "")}
        cards = [{"rod": r, "enchants": v["enchants"], "equipped": v["equipped"]}
                 for r, v in found.items()]
        return {"ok": True, "cards": cards, "cancelled": cancelled,
                "complete": mode == "search" and not cancelled}

    def read_quests(self) -> dict:
        """The Quests tab's "Read now". During a run: the bot's latest read
        (it reads after every cast). Otherwise: bring Roblox forward, close
        an open chat, read the quest tracker once (fischquest.py)."""
        if self._worker and self._worker.is_alive():
            b = self._bot
            view = b.quest_view if b is not None else None
            return {"ok": True, **view} if view else {
                "ok": False, "error": "The bot reads quests at the start of the run and "
                                      "after every cast -- none read yet."}
        error = self._begin_search("quests")
        if error:
            return error
        try:
            return self._read_quests()
        except SearchCancelled:
            return {"ok": False, "cancelled": True, "error": "Quest search cancelled."}
        finally:
            self._end_search()

    def _read_quests(self) -> dict:
        try:
            from fastcap import FastGrabber, find_roblox_window, focus_window
            from fischequip import WinInput
            from fischquest import find_open_chat, quests_view, read_tracker
        except Exception as exc:
            return {"ok": False, "error": f"quest reader unavailable: {exc}"}
        win = find_roblox_window()
        if win is None:
            return {"ok": False, "error": "No Roblox window found."}
        self._search_check()
        focus_window(win[0])
        self._search_check(0.5)
        win = find_roblox_window() or win
        rect = win[1]
        g = FastGrabber(rect)
        try:
            frame = g.grab()
            self._search_check()
            chat = find_open_chat(frame)
            self._search_check()
            if chat is not None:
                WinInput().click(rect.left + chat[0], rect.top + chat[1])
                self._search_check(0.35)
                frame = g.grab()
            self._search_check()
            quests = read_tracker(frame)
            self._search_check()                 # discard cancelled OCR results
        except SearchCancelled:
            raise
        except Exception as exc:
            return {"ok": False, "error": f"could not read the quest tracker: {exc!r}"}
        finally:
            g.close()
        try:
            owned = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("owned", [])
        except (OSError, ValueError):
            owned = []
        if not quests:
            return {"ok": False, "error": "No quest tracker found on the left of the Roblox "
                                          "screen. Track a quest in the Quest Book first."}
        self._log(f"[{datetime.now():%H:%M:%S}] quests read: {len(quests)} tracked")
        return {"ok": True, **quests_view(quests, owned), "read_at": time.time()}

    def get_index(self, kind: str) -> dict:
        """Wiki indexes for the Index tab (fischwiki.py output in ui/)."""
        files = {"mutations": "mutations.json", "weather": "weather.json",
                 "totems": "totems.json", "quests": "quests.json", "fish": "fish.json",
                 "baits": "baits.json"}
        if kind not in files:
            return {"ok": False, "error": "unknown index"}
        try:
            data = json.loads((UI_FILE.parent / files[kind]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"{files[kind]} missing -- run fischwiki.py ({exc})"}
        return {"ok": True, "data": data}

    # --- reel-bar skins (Settings > Reel skins) ---------------------------------
    def get_skins(self) -> dict:
        b = self._bot
        live = b.skins if b is not None and b.skins is not None and self._worker             and self._worker.is_alive() else None
        skins = live.skins if live is not None else clean_skins(load_general().get("skins"))
        return {"ok": True, "skins": skins,
                "current": live.current["id"] if live is not None and live.current else None}

    def edit_skin(self, skin_id: str, name: str = "", forget: bool = False) -> dict:
        """Rename (name) or forget a saved skin. During a run the bot's own
        list is edited too, so its next save keeps the change."""
        b = self._bot
        live = b.skins if b is not None and b.skins is not None and self._worker             and self._worker.is_alive() else None
        skins = live.skins if live is not None else clean_skins(load_general().get("skins"))
        hit = next((s for s in skins if s["id"] == skin_id), None)
        if hit is None:
            return {"ok": False, "error": "no such skin"}
        if forget:
            skins.remove(hit)
            if live is not None and live.current is hit:
                live.current = None
        else:
            name = (name or "").strip()[:40]
            if not name:
                return {"ok": False, "error": "enter a name"}
            hit["name"] = name
        try:
            save_general({"skins": skins})
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return self.get_skins()

    def open_link(self, which: str) -> dict:
        """Open a community link in the default browser (Help page). Only the
        known links -- the page can't ask for any other address."""
        import webbrowser
        links = {"discord": DISCORD_URL, "repo": REPO_URL, "issues": REPO_URL + "/issues",
                 "releases": REPO_URL + "/releases"}
        url = links.get(which)
        if not url:
            return {"ok": False, "error": "not available yet"}
        webbrowser.open(url)
        return {"ok": True}

    def open_logs(self) -> dict:
        """Open the saved_logs folder (runs kept with Keep logs) to attach a
        bot.log to a bug report."""
        import os
        d = Path(__file__).with_name("saved_logs")
        d.mkdir(exist_ok=True)
        os.startfile(str(d))
        return {"ok": True}

    def get_general(self) -> dict:
        """The general config (fischbot_general.json): settings that are not
        part of any saved setup: the Useables tab, the Misc tab, skins, hotkeys."""
        return {"ok": True, "general": load_general()}

    def get_hotkeys(self) -> dict:
        return {"ok": True, "hotkeys": saved_hotkeys(load_general()),
                "available": self._hotkeys is not None and bool(self._hotkeys.handles),
                "error": self._hotkey_error}

    def save_hotkeys(self, data_json: str) -> dict:
        old = saved_hotkeys(load_general())
        try:
            bindings = clean_hotkeys(json.loads(data_json))
            if self._hotkeys is not None:
                self._hotkeys.replace(bindings)
                self._hotkey_error = ""
            try:
                save_general({"hotkeys": bindings})
            except OSError:
                if self._hotkeys is not None:
                    self._hotkeys.replace(old)
                raise
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return self.get_hotkeys()

    def save_general(self, data_json: str) -> dict:
        try:
            data = save_general(json.loads(data_json))
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        b = self._bot
        if b is not None and self._worker and self._worker.is_alive():
            if b.useables is not None:
                b.useables.update(data["useables"])      # limits apply at once
            if getattr(b, "lullaby", None) is not None:
                b.lullaby.update(data["lullaby"])        # the schedule too
        return {"ok": True, "general": data}

    def lullaby_next(self) -> dict:
        """Misc tab: move on to the next buff at the next cast."""
        b = self._bot
        if b is None or getattr(b, "lullaby", None) is None or not (self._worker and self._worker.is_alive()):
            return {"ok": False, "error": "Start a run first."}
        b.lullaby.skip()
        self._note("lullaby: switching to the next buff after this cast")
        return {"ok": True}

    def scan_progress(self) -> dict:
        return dict(self._scan)

    def stop(self) -> None:
        with self._start_lock:
            self._stop()

    def _stop(self) -> None:
        self._stop_requested.set()
        if self._search.get("busy"):
            self._search["cancelling"] = True
            self._log("cancelling " + self._search["kind"] + " search...")
        self.cancel_configuration()
        if self._bot is not None and self._bot.running:
            self._log("stopping...")
            self._bot.stop()

    def get_state(self, since: int = 0) -> dict:
        b = self._bot
        running = bool(self._worker and self._worker.is_alive())
        with self._lock:
            logs = [l for l in self._logs if l[0] > since]
            active = self._active_config
            pending = self._pending_config
        if b is not None and self._run is not None:
            self._run.update(caught=b.caught, lost=b.lost, casts=b.cycles)
        return {
            "running": running,
            "search": dict(self._search),
            "active_config": active,
            "pending_config": pending,
            "rotation": self._rotation_view() if running else None,
            "state": (b.state if b is not None and running
                      else "starting" if running else "idle"),
            "caught": b.caught if b else 0,
            "lost": b.lost if b else 0,
            "casts": b.cycles if b else 0,
            "live": dict(b.live) if b else None,
            "history": list(b.reel_history) if b else [],
            "quests": b.quest_view if b is not None else None,
            "mutations": dict(b.mutations) if b is not None else {},
            "useables": b.useables.view() if b is not None and b.useables is not None else None,
            "lullaby": (b.lullaby.view() if running and getattr(b, "lullaby", None) is not None
                        else None),
            "skin": (b.skins.current["name"] if b is not None and b.skins is not None
                     and b.skins.current else None),
            "weather": b.weather.state.view() if b is not None else None,
            "max_fish": self._run["max_fish"] if self._run else 0,
            "started": self._run["started"] if self._run else None,
            "calibrated": self._calibrated,
            "runs": [dict(r) for r in reversed(self._runs)],
            "logs": logs,
            "now": time.time(),
        }

    # --- worker ------------------------------------------------------------------
    def _note(self, msg: str) -> None:
        """A run event from the UI side (setup queued/applied): through the bot's
        log while a run is on, so it lands in bot.log with the bot's own lines."""
        b = self._bot
        if b is not None and self._worker and self._worker.is_alive():
            b.log(msg)
        else:
            self._log(msg)

    def _log(self, line: str) -> None:
        if "calibration:" in line and ("OK" in line or "adopted" in line):
            self._calibrated = True
        with self._lock:
            self._seq += 1
            self._logs.append((self._seq, line))
            del self._logs[:-MAX_LOG_LINES]

    def _work(self, cfg, ccfg, rod, keep, enchants, extras=None) -> None:
        session = Session(keep_logs=keep)
        if session.swept:
            self._log(f"removed {session.swept} leftover temp folder(s) from an "
                      f"earlier run")
        try:
            self._bot = create_bot(cfg, ccfg, rod, session, on_log=self._log,
                                   enchants=enchants)
            self._bot.on_cycle_boundary = self._on_cycle_boundary
            extras = extras or {}
            self._bot.track_quests = extras.get("track_quests", True)
            self._bot.default_skin = extras.get("default_skin", False)
            self._bot.owned_rods = extras.get("owned", [])
            # Useables come from the general config, never from the setup
            general = load_general()
            self._bot.useables = Useables(general["useables"], self._bot.log)
            self._bot.lullaby = LullabyBuffs(general["lullaby"], self._bot.log)
            self._bot.skins = SkinBook(general.get("skins", []), self._bot.log,
                                       save=lambda sk: save_general({"skins": sk}))
            if self._stop_requested.is_set():
                return
            self._bot.run()
        except NoRobloxWindow as exc:
            self._log(f"error: {exc}")
        except Exception as exc:                      # surface, never hang the UI
            self._log(f"error: {exc!r}")
        finally:
            self.cancel_configuration()
            with self._lock:
                self._rotation = None
            if self._bot is not None:
                self._bot.close_log()
                self._bot.mouse.release()
                self._bot.mouse.restore_cursor()
                self._bot.grabber.close()
                self._run.update(caught=self._bot.caught, lost=self._bot.lost,
                                 casts=self._bot.cycles)
            session.close()
            self._run["ended"] = time.time()
            self._log(f"[{datetime.now():%H:%M:%S}] temporary files deleted"
                      + (f"; logs kept in {session.kept_to}" if session.kept_to else ""))


# Community links (Help page). Only these addresses can be opened from the app.
DISCORD_URL = "https://discord.gg/avBvJjEWbm"   # the community server (never-expiring invite)
REPO_URL = "https://github.com/Derovkov/fisch-bot"
APP_ID = "Derovkov.FischBot"          # Windows taskbar identity (own icon/group)
APP_TITLE = "Fisch bot"
ICON_FILE = Path(__file__).with_name("ui") / "icons" / "app" / "fischbot.ico"


def set_app_identity() -> None:
    """Group the window under its own taskbar entry instead of python's, so
    it shows the app icon (Windows uses the window's icon for an explicit ID)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def apply_window_icon(title: str = APP_TITLE) -> bool:
    """Put ui/icons/app/fischbot.ico on the app window (title bar, taskbar,
    Alt+Tab). pywebview's own icon option is GTK/Qt only."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = u.FindWindowW(None, title)
        if not hwnd or not ICON_FILE.exists():
            return False
        WM_SETICON, IMAGE_ICON, LR_LOADFROMFILE = 0x0080, 1, 0x0010
        for which, size in ((0, u.GetSystemMetrics(49)), (1, u.GetSystemMetrics(11))):  # small, big
            h = u.LoadImageW(None, str(ICON_FILE), IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if h:
                u.SendMessageW(hwnd, WM_SETICON, which, h)
        return True
    except Exception:
        return False


def main() -> None:
    set_app_identity()
    api = Api()
    try:
        import keyboard
        api._hotkeys = Hotkeys(keyboard, {"start": api.start_saved, "stop": api.stop,
                                         "switch": api.cycle_configuration}, on_error=api._log)
        api._hotkeys.replace(saved_hotkeys(load_general()))
    except Exception as exc:
        api._hotkey_error = str(exc)
        api._log(f"could not register all global hotkeys ({exc}); use the UI")
    win = webview.create_window(APP_TITLE, url=str(UI_FILE), js_api=api,
                                width=1280, height=840, min_size=(1080, 720),
                                background_color="#0c0d10")

    def on_closing():
        api.stop()
        if api._hotkeys is not None:
            api._hotkeys.close()
        if api._worker and api._worker.is_alive():
            api._worker.join(timeout=5)

    win.events.closing += on_closing
    win.events.shown += lambda: apply_window_icon()
    webview.start()


if __name__ == "__main__":
    main()
