"""Persistent setups and switching only on the worker's cast boundary."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischconfig import ConfigurationStore, configuration
from fischui import Api, DEFAULTS
from fischbot import FischBot
from fischcontrol import ReelController


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profiles.json'
        self.store = ConfigurationStore(self.path, DEFAULTS, {'Duskwire', 'Crew Rod', 'Fabulous Rod'})
        self.settings = dict(DEFAULTS, rod='Duskwire', rod_enchants={'Duskwire':['Hasty'], 'Crew Rod':['Quality']},
                             owned=['Duskwire', 'Crew Rod'], favs=['Duskwire'])

    def test_round_trip_edit_and_delete_without_inventory_in_snapshot(self):
        row = self.store.save('Steady', self.settings)
        self.assertNotIn('owned', row['settings'])
        self.assertNotIn('favs', row['settings'])
        self.assertEqual(row['settings']['rod_enchants'], {'Duskwire':['Hasty']})
        second = ConfigurationStore(self.path, DEFAULTS, self.store.rods)
        self.assertEqual(second.list(), [row])
        edited = second.save('Steady reel', dict(self.settings, deadband=.1), row['id'])
        self.assertEqual(edited['id'], row['id'])
        self.assertEqual(edited['settings']['deadband'], .1)
        self.assertEqual(len(second.list()), 1)
        self.assertFalse(self.path.with_suffix('.tmp').exists())
        second.delete(row['id'])
        self.assertEqual(second.list(), [])

    def test_invalid_configs_cannot_replace_a_good_saved_setup(self):
        row = self.store.save('Steady', self.settings)
        for changes in [{'deadband':float('nan')}, {'lookahead':-1}, {'max_fish':2.3},
                        {'focus':'bad'}, {'rod':'unknown'}, {'keep':'false'}]:
            with self.assertRaises(ValueError):
                self.store.save('Steady', dict(self.settings, **changes), row['id'])
        self.assertEqual(self.store.list(), [row])
        with self.assertRaises(ValueError):
            self.store.save('steady', self.settings)

    def test_bad_stored_row_does_not_hide_valid_profiles(self):
        row = self.store.save('Steady', self.settings)
        data=json.loads(self.path.read_text())
        data['profiles'].extend([{}, {'id':'bad','name':'Bad','settings':{'rod':'unknown'}}])
        self.path.write_text(json.dumps(data))
        self.assertEqual(self.store.list(), [row])

    def api(self, auto_equip=True):
        # never read or write the user's real settings file
        settings_file = Path(self.temp.name) / 'settings.json'
        settings_file.write_text(json.dumps({'auto_equip': auto_equip}))
        p = patch('fischui.SETTINGS_FILE', settings_file)
        p.start()
        self.addCleanup(p.stop)
        with patch('fischui.PROFILES_FILE', self.path):
            api = Api()
        api._worker = SimpleNamespace(is_alive=lambda: True)
        api._run = dict(rod='Duskwire', max_fish=20)
        api._bot = Mock(running=True)
        api._bot.rod.name = 'Duskwire'
        return api

    def test_queue_changes_nothing_mid_reel_then_applies_at_boundary(self):
        api = self.api()
        request = dict(self.settings, lookahead=.35, max_fish=50)
        self.assertTrue(api.queue_configuration(json.dumps(request))['ok'])
        api._bot.apply_configuration.assert_not_called()
        self.assertEqual(api._run['max_fish'], 20)
        api._apply_pending()
        args = api._bot.apply_configuration.call_args.args
        self.assertEqual(args[0].max_fish, 50)
        self.assertEqual(args[1].lookahead_s, .35)
        self.assertEqual(args[2].name, 'Duskwire')
        self.assertEqual(args[3], ['Hasty'])
        self.assertEqual(api._run['max_fish'], 50)
        self.assertIsNone(api._pending_config)

    def test_latest_switch_wins_and_stop_cancels_a_pending_switch(self):
        api = self.api()
        api.queue_configuration(json.dumps(dict(self.settings, lookahead=.2)))
        api.queue_configuration(json.dumps(dict(self.settings, lookahead=.7)))
        api._apply_pending()
        self.assertEqual(api._bot.apply_configuration.call_args.args[1].lookahead_s, .7)
        api._bot.reset_mock()
        api.queue_configuration(json.dumps(self.settings))
        api.stop()
        api._apply_pending()
        api._bot.apply_configuration.assert_not_called()
        self.assertTrue(api._stop_requested.is_set())

    def test_invalid_queue_does_not_discard_existing_pending_switch(self):
        api = self.api()
        api.queue_configuration(json.dumps(self.settings))
        pending = api._pending_config
        self.assertFalse(api.queue_configuration(json.dumps(dict(self.settings, deadband=-1)))['ok'])
        self.assertEqual(api._pending_config, pending)

    def test_f6_cycles_all_setups_without_touching_live_bot(self):
        # All rods now: with one setup per rod (the user's case) F6 used to do nothing.
        api = self.api()
        first=api._profiles.save('Steady', self.settings)
        second=api._profiles.save('Responsive', dict(self.settings,lookahead=.3))
        other=api._profiles.save('Other rod', dict(self.settings,rod='Crew Rod'))
        api._active_config=dict(id=first['id'],name=first['name'],settings=first['settings'])
        api.cycle_configuration()
        self.assertEqual(api._pending_config['id'],second['id'])
        self.assertEqual(api._pending_config['origin'],'hotkey')
        api._bot.apply_configuration.assert_not_called()
        api._bot.equip_rod.assert_not_called()
        api.cycle_configuration()
        self.assertEqual(api._pending_config['id'],other['id'])
        api.cycle_configuration()
        self.assertEqual(api._pending_config['id'],first['id'])

    def test_f6_says_why_when_there_is_nothing_to_switch_to(self):
        api = self.api()
        only=api._profiles.save('Steady', self.settings)
        api._active_config=dict(id=only['id'],name=only['name'],settings=only['settings'])
        api.cycle_configuration()
        self.assertIsNone(api._pending_config)
        # during a run, UI-side events go through the bot's log (bot.log)
        self.assertIn('only one saved setup', api._bot.log.call_args.args[0])
        api._worker = SimpleNamespace(is_alive=lambda: False)
        api.cycle_configuration()
        self.assertIn('start the bot first', api._logs[-1][1])

    def test_rod_change_equips_first_and_keeps_old_setup_if_that_fails(self):
        api = self.api()
        crew = dict(self.settings, rod='Crew Rod')
        api._bot.equip_rod.return_value = False
        api.queue_configuration(json.dumps(crew))
        api._apply_pending()
        api._bot.equip_rod.assert_called_once_with('Crew Rod')
        api._bot.apply_configuration.assert_not_called()
        self.assertIn('NOT applied', api._bot.log.call_args.args[0])
        api._bot.equip_rod.return_value = True
        api.queue_configuration(json.dumps(crew))
        api._apply_pending()
        self.assertEqual(api._bot.apply_configuration.call_args.args[2].name, 'Crew Rod')

    def test_same_rod_or_auto_equip_off_never_opens_the_bag(self):
        api = self.api()
        api.queue_configuration(json.dumps(dict(self.settings, lookahead=.3)))
        api._apply_pending()
        api._bot.equip_rod.assert_not_called()
        api = self.api(auto_equip=False)
        api.queue_configuration(json.dumps(dict(self.settings, rod='Crew Rod')))
        api._apply_pending()
        api._bot.equip_rod.assert_not_called()
        api._bot.apply_configuration.assert_called_once()

    def test_actual_bot_updates_controller_focus_and_log_lifecycle(self):
        b=FischBot.__new__(FischBot)
        b.mouse=Mock();b.trace_file=None;b.session=SimpleNamespace(path=lambda name:Path(self.temp.name)/name,keep_logs=False)
        b.focus=SimpleNamespace(mode='pin');b.ctl=ReelController();b.catch_watch=SimpleNamespace(rod='Duskwire')
        api=self.api()
        s=configuration(dict(self.settings,trace=True,keep=True,focus='yield',lookahead=.2),DEFAULTS,self.store.rods)
        cfg,ccfg=api._configs(s)
        b.apply_configuration(cfg,ccfg,SimpleNamespace(name='Crew Rod'),[],True)
        self.assertEqual(b.ctl.cfg.lookahead_s,.2)
        self.assertEqual(b.focus.mode,'yield')
        self.assertTrue(b.session.keep_logs)
        self.assertIsNotNone(b.trace_file)
        trace=b.trace_file
        s['trace']=False
        b.apply_configuration(*api._configs(s),SimpleNamespace(name='Duskwire'),['Hasty'],False)
        self.assertTrue(trace.closed)
        self.assertIsNone(b.trace_file)
        self.assertFalse(b.session.keep_logs)
        self.assertEqual(b.enchants,['Hasty'])


if __name__ == '__main__': unittest.main()
