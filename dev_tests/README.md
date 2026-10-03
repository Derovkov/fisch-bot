# Offline tracker checks

These tools read saved recording/crop fixtures and never capture the screen or
send mouse input. Local run logs and images stay out of Git.

Run the focused regressions from the project root:

```powershell
python -m unittest discover -s dev_tests -p "test_*.py"
```

Tests skip with a reason if the required local `saved_logs` crop is absent.
The progress tests cover the real dark-fill Duskwire and bright-fill Crew bars,
the six user-confirmed Duskwire catches, and per-reel colour reset. Controller
checks exercise elapsed-time velocity filtering and reset after reacquisition.
`test_catch_notifications.py` covers the supplied Duskwire screenshot's actual
OCR output, the normal catch-message fallback, stale/repeated notifications,
late results from previous attempts, OCR failure/timeout, and ending the reel
on confirmed catch text despite a lingering bar and low reported progress.
These unit checks don't need Windows OCR or an active game window.
`test_configurations.py` checks persistent configuration snapshots, validation,
inventory separation, queued-switch timing/cancellation, F6 cycling for the
current rod, and the controller/focus/log lifecycle at a cast boundary.
`ui_smoke.cjs` exercises the HTML with a mocked bridge in headless Edge at
1280×840 and 1080×720. It requires Playwright and `tmp/ui_meta_fixture.json`
(JSON from `Api().get_meta()`). It never sends game input or captures the screen.
`tracker_test.py` accepts run folder paths and reports manually labelled crop
accuracy, including frames rejected rather than guessed. It returns a failure
for an incorrect accepted reading. Missing/occluded readings are reported
separately and are not successful detections.

To compare the default Fabulous reader against an earlier source file using
the original 1920×1080 recording (client rows 23:1032):

```powershell
python dev_tests/compare_fabulous.py BASELINE_FISCHTRACK.py RECORDING.mp4
```

Sparse saved crops cannot reproduce every intermediate motion or feedback
decision. Live rod runs remain necessary to evaluate catches and control.
