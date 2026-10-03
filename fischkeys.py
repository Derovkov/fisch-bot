"""App-wide shortcuts, independently of rod setups. Hooks are owned/removable.
Offline tests inject a keyboard backend; importing this installs no hooks.
"""
import re
import threading
import time

DEFAULT_HOTKEYS = {"start": "f7", "stop": "f9", "switch": "f6"}
MODIFIERS = ("ctrl", "alt", "shift", "windows")
NAMED_KEYS = {"space", "tab", "enter", "esc", "insert", "delete", "home", "end",
              "page up", "page down", "up", "down", "left", "right", "backspace"}


def clean_hotkeys(raw):
    if not isinstance(raw, dict):
        raise ValueError("shortcuts must be an object")
    out = {}
    aliases = {"control": "ctrl", "win": "windows", "escape": "esc",
               "return": "enter", "pageup": "page up", "pagedown": "page down"}
    for action, default in DEFAULT_HOTKEYS.items():
        value = raw.get(action, default)
        if not isinstance(value, str):
            raise ValueError(f"{action}: enter a key or combination")
        parts = [aliases.get(p.strip().lower(), p.strip().lower()) for p in value.split("+")]
        keys = [p for p in parts if p not in MODIFIERS]
        if len(set(parts)) != len(parts) or len(keys) != 1:
            raise ValueError(f"{action}: use modifiers plus one key, e.g. ctrl+alt+f7")
        key = keys[0]
        if not (key in NAMED_KEYS or re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])", key)):
            raise ValueError(f"{action}: unknown key '{key}'")
        out[action] = "+".join([m for m in MODIFIERS if m in parts] + [key])
    if len(set(out.values())) != len(out):
        raise ValueError("use a different shortcut for Start, Stop and Switch")
    return out


def saved_hotkeys(general):
    try:
        return clean_hotkeys(general.get("hotkeys", {}))
    except ValueError:
        return dict(DEFAULT_HOTKEYS)


def _mod(name):
    """'left ctrl' / 'right shift' / 'alt gr' / 'left windows' -> modifier, else None."""
    n = (name or "").lower()
    for m in MODIFIERS:
        if m in n or (m == "alt" and n == "alt gr"):
            return m
    return None


def _thread(fn):
    threading.Thread(target=fn, daemon=True, name="fischkeys-action").start()


class Hotkeys:
    """Global shortcuts from ONE raw keyboard hook, matched here.

    keyboard.add_hotkey was unreliable (user, 2026-10-03: only Stop answered,
    and only sometimes). Its non-blocking hotkeys look the currently held keys
    up when its worker thread gets to the event, but the hook thread has
    already updated that table: on release the key is gone (trigger_on_release
    never matched), and a quick tap is released before its press is processed.
    Here the held keys are tracked from the event stream itself, in order, so
    nothing races; a shortcut fires on the key's press (repeats ignored) and
    the action runs on its own thread, so a slow Start can't stall the hook
    (Windows drops a low-level hook that takes too long)."""

    def __init__(self, backend, callbacks, clock=time.monotonic, on_error=None,
                 dispatch=_thread):
        self.backend, self.callbacks, self.clock = backend, callbacks, clock
        self.on_error = on_error or (lambda message: None)
        self.dispatch = dispatch
        self.bindings = dict(DEFAULT_HOTKEYS)
        self.handles = []
        self._token = None
        self._last = {}
        self._lock = threading.RLock()
        self._mods: set = set()
        self._down: set = set()
        self._keys: dict = {}            # action -> (modifiers, scan codes, name)

    def replace(self, raw):
        bindings = clean_hotkeys(raw)
        with self._lock:
            if self.handles and bindings == self.bindings:
                return dict(bindings)
            old = dict(self.bindings)
            had_hooks = bool(self.handles)
            self.close()
            try:
                self._install(bindings)
            except Exception:
                if had_hooks:
                    self._install(old)
                raise
        return dict(bindings)

    def _parse(self, combo):
        parts = combo.split("+")
        key = parts[-1]
        return frozenset(parts[:-1]), frozenset(self.backend.key_to_scan_codes(key)), key

    def _install(self, bindings):
        keys = {action: self._parse(combo) for action, combo in bindings.items()}
        token = object()
        handle = self.backend.hook(lambda e, token=token: self._event(e, token))
        with self._lock:
            self.handles, self.bindings, self._token, self._keys = [handle], bindings, token, keys
            self._last.clear()
            self._mods.clear()
            self._down.clear()

    def _event(self, e, token):
        try:
            down = getattr(e, "event_type", "") == "down"
            name = (getattr(e, "name", "") or "").lower()
            code = getattr(e, "scan_code", None)
            mod = _mod(name)
            with self._lock:
                if token is not self._token:
                    return
                if mod:
                    (self._mods.add if down else self._mods.discard)(mod)
                    return
                ident = code if code is not None else name
                if not down:
                    self._down.discard(ident)
                    return
                if ident in self._down:
                    return                       # auto-repeat while held
                self._down.add(ident)
                hits = [a for a, (mods, codes, key) in self._keys.items()
                        if mods == self._mods and (code in codes or (not codes and name == key))]
                now = self.clock()
                fire = []
                for action in hits:
                    if now - self._last.get(action, -float("inf")) >= .5:
                        self._last[action] = now
                        fire.append(action)
            for action in fire:
                self.dispatch(lambda action=action: self._run(action))
        except Exception as exc:                 # never raise inside the hook
            self.on_error(f"shortcut handling failed: {exc}")

    def _run(self, action):
        try:
            self.callbacks[action]()
        except Exception as exc:
            self.on_error(f"{action} shortcut failed: {exc}")

    def close(self):
        with self._lock:
            self._token = None
            old, self.handles = self.handles, []
            for handle in old:
                self.backend.unhook(handle)
