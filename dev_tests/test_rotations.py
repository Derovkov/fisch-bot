"""Rod rotations: fish N minutes with each rod, switching only at cast boundaries."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fischconfig import ConfigurationStore, rotation_settings
from fischui import Api, DEFAULTS

RODS = {'Duskwire', 'Crew Rod', 'Fabulous Rod'}


def plan(*steps, **base):
    return {'base': dict(DEFAULTS, **base),
            'steps': [{'rod': r, 'enchants': e, 'minutes': m} for r, e, m in steps]}


class RotationStoreTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profiles.json'
        self.store = ConfigurationStore(self.path, DEFAULTS, RODS)

    def test_round_trip_keeps_setups_and_rotations_apart(self):
        setup = self.store.save('Steady', dict(DEFAULTS, rod='Duskwire'))
        rot = self.store.save_rotation('Grind', plan(('Duskwire', ['Hasty'], 20), ('Crew Rod', [], 7.5),
                                                     lookahead=.3))
        self.assertEqual([s['rod'] for s in rot['steps']], ['Duskwire', 'Crew Rod'])
        self.assertEqual(rot['base']['lookahead'], .3)
        self.assertNotIn('rod', rot['base'])
        again = ConfigurationStore(self.path, DEFAULTS, RODS)
        self.assertEqual(again.list(), [setup])            # saving a rotation kept the setup
        self.assertEqual(again.list_rotations(), [rot])
        again.save('Second', dict(DEFAULTS, rod='Crew Rod'))
        self.assertEqual(again.list_rotations(), [rot])    # and saving a setup kept the rotation
        step = rotation_settings(rot, 1)
        self.assertEqual((step['rod'], step['rod_enchants'], step['lookahead']), ('Crew Rod', {'Crew Rod': []}, .3))
        again.delete_rotation(rot['id'])
        self.assertEqual(again.list_rotations(), [])
        self.assertEqual(len(again.list()), 2)

    def test_invalid_rotations_are_refused(self):
        bad = [plan(('Duskwire', [], 10)),                                  # one rod
               plan(*[('Duskwire', [], 5)] * 11),                           # too many
               plan(('Duskwire', [], 0), ('Crew Rod', [], 5)),              # no time
               plan(('Duskwire', [], 5), ('Crew Rod', [], 601)),
               plan(('Duskwire', [], True), ('Crew Rod', [], 5)),
               plan(('Duskwire', [], 5), ('Unknown', [], 5)),
               plan(('Duskwire', ['a', 'b', 'c', 'd'], 5), ('Crew Rod', [], 5))]
        for data in bad:
            with self.assertRaises(ValueError):
                self.store.save_rotation('Bad', data)
        self.store.save_rotation('Grind', plan(('Duskwire', [], 5), ('Crew Rod', [], 5)))
        with self.assertRaises(ValueError):
            self.store.save_rotation('grind', plan(('Duskwire', [], 5), ('Crew Rod', [], 5)))


class RotationRunTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profiles.json'
        self.clock = [1000.0]
        p = patch('fischui.time.time', lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)

    def api(self, auto_equip=True):
        settings_file = Path(self.temp.name) / 'settings.json'
        settings_file.write_text(json.dumps({'auto_equip': auto_equip}))
        p = patch('fischui.SETTINGS_FILE', settings_file)
        p.start()
        self.addCleanup(p.stop)
        with patch('fischui.PROFILES_FILE', self.path):
            api = Api()
        api._profiles.rods = RODS
        api._worker = SimpleNamespace(is_alive=lambda: True)
        api._run = dict(rod='Duskwire', max_fish=20)
        bot = api._bot = Mock(running=True)
        bot.rod.name = 'Duskwire'
        bot.equip_rod.return_value = True

        def applied(cfg, ccfg, rod, enchants, keep):
            bot.rod.name = rod.name
        bot.apply_configuration.side_effect = applied
        return api

    def grind(self, api, *steps):
        return api._profiles.save_rotation('Grind', plan(*(steps or (('Duskwire', [], 10), ('Crew Rod', ['Quality'], 5)))))

    def start(self, api, settings):
        """Api._start without a worker thread; the mock bot stays in place."""
        bot, api._worker = api._bot, None
        with patch('fischui.threading.Thread'):
            r = api._start(json.dumps(settings))
        api._bot, api._worker = bot, SimpleNamespace(is_alive=lambda: True)
        return r

    def tick(self, api, minutes=0):
        self.clock[0] += minutes * 60
        api._on_cycle_boundary()

    def test_start_with_a_rotation_uses_its_first_rod_and_switches_on_time(self):
        api = self.api()
        rot = self.grind(api, ('Crew Rod', [], 10), ('Duskwire', [], 5))
        r = self.start(api, dict(DEFAULTS, rod='Fabulous Rod', active_rotation=rot['id']))
        self.assertTrue(r['ok'], r)
        self.assertEqual(api._active_config['settings']['rod'], 'Crew Rod')
        self.assertEqual(api._rotation_view()['left_s'], 600)
        api._bot.rod.name = 'Crew Rod'
        self.tick(api, 9.9)
        api._bot.apply_configuration.assert_not_called()      # not yet
        self.tick(api, 0.2)
        self.assertEqual(api._bot.rod.name, 'Duskwire')
        api._bot.equip_rod.assert_called_once_with('Duskwire')
        view = api._rotation_view()
        self.assertEqual((view['index'], view['left_s'], view['next_rod']), (1, 300, 'Crew Rod'))
        self.tick(api, 5)                                      # last rod -> back to the first
        self.assertEqual(api._bot.rod.name, 'Crew Rod')
        self.assertEqual(api._rotation_view()['index'], 0)

    def test_queue_during_a_run_then_a_setup_ends_it(self):
        api = self.api()
        rot = self.grind(api)
        self.assertTrue(api.queue_rotation(rot['id'])['ok'])
        self.assertIsNone(api._rotation)                       # not before the cast ends
        self.tick(api)
        self.assertEqual(api._rotation_view()['name'], 'Grind')
        self.assertEqual(api._active_config['rotation'], {'id': rot['id'], 'step': 0})
        api.queue_configuration(json.dumps(dict(DEFAULTS, rod='Duskwire', lookahead=.2)))
        self.tick(api)
        self.assertIsNone(api._rotation)
        self.assertIn('ended by the switch', api._bot.log.call_args.args[0])

    def test_a_rod_that_cannot_be_equipped_is_skipped(self):
        api = self.api()
        rot = self.grind(api, ('Duskwire', [], 10), ('Crew Rod', [], 5), ('Fabulous Rod', [], 5))
        api.queue_rotation(rot['id'])
        self.tick(api)
        api._bot.equip_rod.side_effect = lambda name: name != 'Crew Rod'
        self.tick(api, 10)                                     # Crew Rod fails...
        self.assertEqual(api._bot.rod.name, 'Duskwire')
        self.tick(api)                                         # ...so the next cast tries Fabulous Rod
        self.assertEqual(api._bot.rod.name, 'Fabulous Rod')
        self.assertEqual(api._rotation_view()['index'], 2)

    def test_when_no_other_rod_can_be_equipped_it_stays_a_full_turn(self):
        api = self.api()
        rot = self.grind(api)
        api.queue_rotation(rot['id'])
        self.tick(api)
        api._bot.equip_rod.return_value = False
        self.tick(api, 10)
        self.assertEqual(api._bot.rod.name, 'Duskwire')
        self.assertIn('staying on Duskwire for another 10 min', api._bot.log.call_args.args[0])
        api._bot.equip_rod.reset_mock()
        self.tick(api, 9)
        api._bot.equip_rod.assert_not_called()                 # no bag spam every cast

    def test_needs_equip_rod_in_game(self):
        api = self.api(auto_equip=False)
        rot = self.grind(api)
        self.assertIn('Equip rod in game', api.queue_rotation(rot['id'])['error'])
        self.assertIn('Equip rod in game', self.start(api, dict(DEFAULTS, active_rotation=rot['id']))['error'])

    def test_stop_rotating_keeps_the_current_rod(self):
        api = self.api()
        rot = self.grind(api)
        api.queue_rotation(rot['id'])
        self.tick(api)
        api.end_rotation()
        self.tick(api, 60)
        self.assertIsNone(api._rotation)
        self.assertEqual(api._bot.rod.name, 'Duskwire')


if __name__ == '__main__':
    unittest.main()
