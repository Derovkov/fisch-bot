"""Config validation and owned global hooks; never installs real hooks."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischkeys import Hotkeys, clean_hotkeys, DEFAULT_HOTKEYS
from fischui import Api, DEFAULTS
from fischuse import load_general, save_general


class FakeKeyboard:
    """One raw hook, like keyboard.hook; fire() sends press/release events."""

    def __init__(self):
        self.hooks, self.next = {}, 0
        self.fail = None

    def key_to_scan_codes(self, key):
        if key == self.fail:
            raise RuntimeError("registration failed")
        return (sum(map(ord, key)) * 7 % 997 + 1,)

    def hook(self, fn):
        self.next += 1
        self.hooks[self.next] = fn
        return self.next

    def unhook(self, handle):
        del self.hooks[handle]

    def send(self, name, down):
        code = None if name in ("ctrl", "alt", "shift", "windows") else self.key_to_scan_codes(name)[0]
        e = SimpleNamespace(event_type="down" if down else "up", name=name, scan_code=code)
        for fn in list(self.hooks.values()):
            fn(e)

    def fire(self, combo):
        *mods, key = combo.split("+")
        for m in mods:
            self.send(m, True)
        self.send(key, True)
        self.send(key, False)
        for m in reversed(mods):
            self.send(m, False)


def sync(fn):
    fn()


class KeyTests(unittest.TestCase):
    def test_real_keyboard_hook_quick_tap_and_held_modifiers(self):
        # The installed keyboard library's hook/unhook with its worker-thread
        # handler list; no OS hook is started. A quick tap -- released before
        # its press is processed, which keyboard.add_hotkey missed -- fires.
        import keyboard
        from keyboard import KeyboardEvent
        events = []
        with patch.object(keyboard._listener, "start_if_necessary"):
            h = Hotkeys(keyboard, {a: lambda a=a: events.append(a) for a in DEFAULT_HOTKEYS},
                        dispatch=sync)
            h.replace({"start": "ctrl+alt+r"})
            self.assertEqual(len(keyboard._listener.handlers), 1)

            def ev(kind, name):
                code = keyboard.key_to_scan_codes(name)[0]
                for fn in list(keyboard._listener.handlers):
                    fn(KeyboardEvent(kind, code, name=name))
            ev("down", "f9"); ev("up", "f9")                 # quick tap
            ev("down", "f9"); ev("down", "f9"); ev("up", "f9")   # auto-repeat: once
            ev("down", "r"); ev("up", "r")                   # no modifiers: nothing
            ev("down", "ctrl"); ev("down", "alt"); ev("down", "r"); ev("up", "r")
            ev("up", "alt"); ev("up", "ctrl")
            self.assertEqual(events, ["stop", "start"])
            for _ in range(3):
                h.replace({"start": "f8"})
                h.replace({"start": "ctrl+alt+r"})
            self.assertEqual(len(keyboard._listener.handlers), 1)
            h.close()
            self.assertEqual(len(keyboard._listener.handlers), 0)

    def test_callback_failure_does_not_prevent_stop(self):
        kb, errors, stopped = FakeKeyboard(), [], Mock()
        h = Hotkeys(kb, {"start": Mock(side_effect=RuntimeError("failed start")),
                        "stop": stopped, "switch": Mock()}, on_error=errors.append, dispatch=sync)
        h.replace({})
        kb.fire("f7"); kb.fire("f9")
        self.assertIn("failed start", errors[0])
        stopped.assert_called_once()
        h.close()

    def test_canonical_and_duplicate_validation(self):
        self.assertEqual(clean_hotkeys({}), DEFAULT_HOTKEYS)
        self.assertEqual(clean_hotkeys({"start": "SHIFT + CONTROL + R"})["start"], "ctrl+shift+r")
        for raw in ({"start": "f9"}, {"start": "alt+ctrl+r", "stop": "ctrl+alt+r"},
                    {"start": "ctrl+ctrl+f7"}, {"start": "f25"}, {"start": ""}, {"start": "ctrl"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                clean_hotkeys(raw)

    def test_rebind_removes_old_hooks_and_debounces_release(self):
        kb, events, tick = FakeKeyboard(), [], [0.]
        h = Hotkeys(kb, {a: lambda a=a: events.append(a) for a in DEFAULT_HOTKEYS},
                    clock=lambda: tick[0], dispatch=sync)
        h.replace({})
        kb.fire("f7"); kb.fire("f7")
        self.assertEqual(events, ["start"])
        tick[0] = 1.
        h.replace({"start": "ctrl+alt+r"})
        kb.fire("f7"); kb.fire("ctrl+alt+r")
        self.assertEqual(events, ["start", "start"])
        self.assertEqual(len(kb.hooks), 1)
        h.close()
        self.assertEqual(kb.hooks, {})

    def test_failed_registration_keeps_old_shortcuts(self):
        kb, events = FakeKeyboard(), []
        h = Hotkeys(kb, {a: lambda a=a: events.append(a) for a in DEFAULT_HOTKEYS}, dispatch=sync)
        h.replace({})
        kb.fail = "f10"
        with self.assertRaises(RuntimeError):
            h.replace({"start": "f8", "stop": "f10"})
        self.assertEqual(len(kb.hooks), 1)
        kb.fire("f7")
        self.assertEqual(events, ["start"])
        self.assertEqual(h.bindings, DEFAULT_HOTKEYS)
        h.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.tmp.cleanup)
        self.general = Path(self.tmp.name) / "general.json"
        self.settings = Path(self.tmp.name) / "settings.json"
        for target, value in [("fischui.SETTINGS_FILE", self.settings),
                              ("fischui.PROFILES_FILE", Path(self.tmp.name) / "profiles.json"),
                              ("fischui.load_general", lambda: load_general(self.general)),
                              ("fischui.save_general", lambda data: save_general(data, self.general))]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        self.api = Api()

    def test_hotkeys_persist_apart_from_useables_and_profiles(self):
        save_general({"useables": {"enabled": True}}, self.general)
        result = self.api.save_hotkeys(json.dumps({"start": "ctrl+alt+r"}))
        self.assertTrue(result["ok"])
        self.assertEqual(load_general(self.general)["hotkeys"]["start"], "ctrl+alt+r")
        self.assertTrue(load_general(self.general)["useables"]["enabled"])
        self.assertEqual(self.api.get_meta()["hotkeys"]["start"], "ctrl+alt+r")
        self.assertNotIn("hotkeys", DEFAULTS)
        self.assertFalse(self.api.save_hotkeys(json.dumps({"start": "f9"}))["ok"])
        self.assertEqual(self.api.get_hotkeys()["hotkeys"]["start"], "ctrl+alt+r")

    def test_start_shortcut_reads_latest_saved_settings_and_scan_blocks_start(self):
        self.settings.write_text(json.dumps(dict(DEFAULTS, rod="Duskwire")), encoding="utf-8")
        with patch.object(self.api, "start", return_value={"ok": True}) as start:
            self.api.start_saved()
            self.assertEqual(json.loads(start.call_args.args[0])["rod"], "Duskwire")
        self.api._scan = {"busy": True}
        self.assertIn("scan", self.api.start(json.dumps(DEFAULTS))["error"])

    def test_failed_hook_registration_does_not_save_new_keys(self):
        kb = FakeKeyboard()
        self.api._hotkeys = Hotkeys(kb, {a: Mock() for a in DEFAULT_HOTKEYS})
        self.api._hotkeys.replace({})
        kb.fail = "f10"
        result = self.api.save_hotkeys(json.dumps({"start": "f8", "stop": "f10"}))
        self.assertFalse(result["ok"])
        self.assertEqual(self.api.get_hotkeys()["hotkeys"], DEFAULT_HOTKEYS)
        self.assertEqual(len(kb.hooks), 1)
        self.api._hotkeys.close()

    def test_stop_cancels_quest_ocr_and_discards_result(self):
        frame, g = object(), Mock()
        g.grab.return_value = frame
        kb = FakeKeyboard()
        keys = Hotkeys(kb, {"start": self.api.start_saved, "stop": self.api.stop,
                            "switch": self.api.cycle_configuration}, dispatch=sync)
        keys.replace({"stop": "f10"})
        self.addCleanup(keys.close)
        rect = SimpleNamespace(left=10, top=10)
        original = self.api._search_check
        def read(_):
            self.assertTrue(self.api.get_state()["search"]["busy"])
            self.assertFalse(self.api.start(json.dumps(DEFAULTS))["ok"])
            self.assertFalse(self.api.scan_rods()["ok"])
            kb.fire("f10")
            return [object()]
        with patch("fastcap.find_roblox_window", return_value=(1, rect)), \
             patch("fastcap.focus_window"), patch("fastcap.FastGrabber", return_value=g), \
             patch("fischquest.find_open_chat", return_value=None), \
             patch("fischquest.read_tracker", side_effect=read), \
             patch("fischquest.quests_view") as view, \
             patch.object(self.api, "_search_check", side_effect=lambda wait=0: original()):
            r = self.api.read_quests()
        self.assertTrue(r["cancelled"])
        self.assertFalse(r["ok"])
        view.assert_not_called()
        g.close.assert_called_once()
        self.assertFalse(self.api.get_state()["search"]["busy"])

    def test_stop_before_rod_focus_and_next_search_gets_fresh_event(self):
        rect = SimpleNamespace(left=0, top=0)
        def find():
            self.api.stop()
            return (1, rect)
        with patch("fastcap.find_roblox_window", side_effect=find), \
             patch("fastcap.focus_window") as focus, patch("fastcap.FastGrabber") as grab:
            r = self.api.scan_rods("search")
        self.assertTrue(r["cancelled"])
        focus.assert_not_called(); grab.assert_not_called()
        self.assertFalse(self.api.scan_progress()["busy"])
        self.assertIsNone(self.api._begin_search("quests"))
        self.assertFalse(self.api._stop_requested.is_set())
        self.api.stop()
        self.assertFalse(self.api._begin_search("rods")["ok"])
        self.assertTrue(self.api._stop_requested.is_set())
        self.api._end_search()

    def test_stop_rod_scan_keeps_partial_cards_and_closes_capture(self):
        g, menu = Mock(), Mock()
        rect = SimpleNamespace(left=0, top=0)
        def scan(*_):
            self.api.stop()
            return {"Duskwire": {"enchants": [], "equipped": True}}
        menu.scan_by_search.side_effect = scan
        context = Mock(); context.__enter__ = Mock(return_value=menu)
        context.__exit__ = Mock(return_value=False)
        original = self.api._search_check
        with patch("fastcap.find_roblox_window", return_value=(1, rect)), \
             patch("fastcap.focus_window"), patch("fastcap.FastGrabber", return_value=g), \
             patch("fischequip.EquipmentMenu", return_value=context), \
             patch.object(self.api, "_search_check", side_effect=lambda wait=0: original()):
            r = self.api.scan_rods("search")
        self.assertTrue(r["ok"]); self.assertTrue(r["cancelled"])
        self.assertFalse(r["complete"])
        self.assertEqual(r["cards"][0]["rod"], "Duskwire")
        g.close.assert_called_once(); context.__exit__.assert_called_once()
        self.assertFalse(self.api.get_state()["search"]["busy"])


if __name__ == "__main__":
    unittest.main()
