"""Small persistent configuration snapshots, separate from run data/inventory."""
from copy import deepcopy
import json
import math
from pathlib import Path
from threading import RLock
from uuid import uuid4

CONFIG_KEYS = ('rod', 'max_fish', 'focus', 'debug', 'trace', 'keep',
               'lookahead', 'deadband', 'bite')


def configuration(settings, defaults, rods):
    if not isinstance(settings, dict):
        raise ValueError('Settings must be an object.')
    out = {key: settings.get(key, defaults[key]) for key in CONFIG_KEYS}
    if out['rod'] not in rods:
        raise ValueError('Choose a known rod.')
    if out['focus'] not in ('pin', 'yield'):
        raise ValueError('Choose a valid focus mode.')
    for key, low, high in [('max_fish', 0, 100000), ('lookahead', 0, 1.5),
                           ('deadband', 0, .3), ('bite', 5, 120)]:
        value = out[key]
        if isinstance(value, bool):
            raise ValueError(f'Invalid {key}.')
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'Invalid {key}.') from None
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be between {low} and {high}.')
        if key == 'max_fish' and value != int(value):
            raise ValueError('Cast count must be a whole number.')
        out[key] = int(value) if key == 'max_fish' else value
    for key in ('debug', 'trace', 'keep'):
        if not isinstance(out[key], bool):
            raise ValueError(f'Invalid {key}.')
    enchants = settings.get('rod_enchants', {})
    if not isinstance(enchants, dict):
        raise ValueError('Invalid enchants.')
    names = enchants.get(out['rod'], [])
    if (not isinstance(names, list) or len(names) > 3
            or any(not isinstance(name, str) for name in names)):
        raise ValueError('Choose up to three enchants.')
    out['rod_enchants'] = {out['rod']: list(dict.fromkeys(names))}
    return out


class ConfigurationStore:
    def __init__(self, path: Path, defaults, rods):
        self.path, self.defaults, self.rods = path, defaults, rods
        self.lock = RLock()

    def list(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                rows = data.get('profiles', [])
                if not isinstance(rows, list):
                    return []
            except (OSError, ValueError, AttributeError):
                return []
            valid = []
            for row in rows:
                try:
                    if (not isinstance(row.get('id'), str) or not row['id']
                            or not isinstance(row.get('name'), str) or not row['name'].strip()):
                        continue
                    valid.append(dict(id=row['id'], name=row['name'],
                                      settings=configuration(row['settings'], self.defaults, self.rods)))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            return valid

    def _write(self, rows):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'version': 1, 'profiles': rows}, indent=2,
                                   allow_nan=False), encoding='utf-8')
        temp.replace(self.path)

    def save(self, name, settings, profile_id=''):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
            raise ValueError('Use a name with 1–50 characters.')
        name = name.strip()
        snapshot = configuration(settings, self.defaults, self.rods)
        with self.lock:
            rows = self.list()
            if profile_id and not any(row['id'] == profile_id for row in rows):
                raise ValueError('This configuration no longer exists.')
            if any(row['name'].casefold() == name.casefold() and row['id'] != profile_id
                   for row in rows):
                raise ValueError('That name is already used. Update the existing configuration.')
            row = dict(id=profile_id or uuid4().hex, name=name, settings=snapshot)
            rows = [row if item['id'] == row['id'] else item for item in rows]
            if not profile_id:
                rows.append(row)
            self._write(rows)
            return deepcopy(row)

    def delete(self, profile_id):
        with self.lock:
            rows = self.list()
            self._write([row for row in rows if row['id'] != profile_id])
