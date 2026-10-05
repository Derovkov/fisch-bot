"""Small persistent configuration snapshots, separate from run data/inventory."""
from copy import deepcopy
import json
import math
from pathlib import Path
from threading import RLock
from uuid import uuid4

CONFIG_KEYS = ('rod', 'max_fish', 'focus', 'debug', 'trace', 'keep',
               'lookahead', 'deadband', 'bite', 'fast_cast')
ROTATION_STEPS = (2, 10)             # rods in a rotation
ROTATION_MINUTES = (1, 600)          # time on each rod


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
    for key in ('debug', 'trace', 'keep', 'fast_cast'):
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


def rotation(row, defaults, rods):
    """A rod rotation: fish `minutes` with each step's rod, then switch to the
    next (and back to the first after the last). `base` holds the shared
    settings (cast limit, focus, control, logs); each step brings its rod and
    that rod's enchants. Returns {'base', 'steps'}; ValueError if invalid."""
    if not isinstance(row, dict):
        raise ValueError('Invalid rotation.')
    steps, base = row.get('steps'), row.get('base') or {}
    low, high = ROTATION_STEPS
    if not isinstance(steps, list) or not low <= len(steps) <= high:
        raise ValueError(f'A rotation needs {low} to {high} rods.')
    if not isinstance(base, dict):
        raise ValueError('Invalid rotation settings.')
    out = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Invalid rotation step.')
        minutes = step.get('minutes')
        lo, hi = ROTATION_MINUTES
        if (isinstance(minutes, bool) or not isinstance(minutes, (int, float))
                or not math.isfinite(minutes) or not lo <= minutes <= hi):
            raise ValueError(f'Give each rod {lo} to {hi} minutes.')
        rod = step.get('rod')
        snap = configuration(dict(base, rod=rod, rod_enchants={rod: step.get('enchants', [])}),
                             defaults, rods)
        out.append({'rod': snap['rod'], 'enchants': snap['rod_enchants'][snap['rod']],
                    'minutes': round(float(minutes), 1)})
    shared = configuration(dict(base, rod=out[0]['rod'], rod_enchants={}), defaults, rods)
    del shared['rod'], shared['rod_enchants']
    return {'base': shared, 'steps': out}


def rotation_settings(rot, index):
    """The full setup for step `index` of a validated rotation."""
    step = rot['steps'][index]
    return dict(rot['base'], rod=step['rod'], rod_enchants={step['rod']: list(step['enchants'])})


class ConfigurationStore:
    def __init__(self, path: Path, defaults, rods):
        self.path, self.defaults, self.rods = path, defaults, rods
        self.lock = RLock()

    def _read(self, key):
        try:
            rows = json.loads(self.path.read_text(encoding='utf-8')).get(key, [])
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError, AttributeError):
            return []

    def list(self):
        with self.lock:
            rows = self._read('profiles')
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

    def _write(self, rows=None, rotations=None):
        """Write setups and/or rotations; whichever is None is kept as stored."""
        data = {'version': 1,
                'profiles': self.list() if rows is None else rows,
                'rotations': self.list_rotations() if rotations is None else rotations}
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
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

    # --- rod rotations (Quick switch > Rotations) -------------------------------
    def list_rotations(self):
        with self.lock:
            valid = []
            for row in self._read('rotations'):
                try:
                    if (not isinstance(row.get('id'), str) or not row['id']
                            or not isinstance(row.get('name'), str) or not row['name'].strip()):
                        continue
                    valid.append(dict(id=row['id'], name=row['name'],
                                      **rotation(row, self.defaults, self.rods)))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            return valid

    def save_rotation(self, name, data, rotation_id=''):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
            raise ValueError('Use a name with 1–50 characters.')
        name = name.strip()
        checked = rotation(data, self.defaults, self.rods)
        with self.lock:
            rows = self.list_rotations()
            if rotation_id and not any(row['id'] == rotation_id for row in rows):
                raise ValueError('This rotation no longer exists.')
            if any(row['name'].casefold() == name.casefold() and row['id'] != rotation_id
                   for row in rows):
                raise ValueError('That name is already used by another rotation.')
            row = dict(id=rotation_id or uuid4().hex, name=name, **checked)
            rows = [row if item['id'] == row['id'] else item for item in rows]
            if not rotation_id:
                rows.append(row)
            self._write(rotations=rows)
            return deepcopy(row)

    def delete_rotation(self, rotation_id):
        with self.lock:
            self._write(rotations=[r for r in self.list_rotations() if r['id'] != rotation_id])
