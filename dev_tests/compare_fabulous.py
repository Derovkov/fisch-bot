"""Compare current read_track with a baseline module on the real recording.

Usage: python dev_tests/compare_fabulous.py BASELINE_PY RECORDING_MP4
Offline only: no captures or mouse input, no generated images.
"""
from pathlib import Path
import importlib.util
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fischtrack as current

spec = importlib.util.spec_from_file_location('baseline_track', sys.argv[1])
baseline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = baseline
spec.loader.exec_module(baseline)
for module in (current, baseline):
    module.set_client(1920, 1009)
    module.lock_scale(1.0)
cap = cv2.VideoCapture(sys.argv[2])
if not cap.isOpened():
    raise SystemExit('Cannot open the reference recording')
frames = hits = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    rgb = cv2.cvtColor(frame[23:1032], cv2.COLOR_BGR2RGB)
    old, new = baseline.read_track(rgb), current.read_track(rgb)
    assert (old is None) == (new is None), f'reading presence changed at frame {frames}'
    if old is not None:
        assert vars(old) == vars(new), f'reading changed at frame {frames}'
        hits += 1
    frames += 1
cap.release()
assert frames > 0
print(f'Fabulous regression: {frames} frames identical, {hits} bar readings')
