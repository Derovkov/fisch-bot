"""Read catch notifications without blocking the reel servo or saving images.

One daemon worker owns OCR, with at most one image in flight. Results belong to
an attempt and a capture time. A notification must clear before another catch
can be confirmed, including when successive catches show the same passive text.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from queue import Empty, Queue
import re
from threading import Event, Thread
from typing import Callable, Optional

import numpy as np

# Attribute / mutation / fish splitting is shared with the quest reader;
# ATTRIBUTES and mutation_names are re-exported for older callers.
from fischnames import ATTRIBUTES, mutation_names, split_catch  # noqa: F401


@dataclass(frozen=True)
class CatchNotice:
    source: str
    # What was caught, read from "You just caught a Chaotic Pike at 1.2kg!"
    # (None when that line was not read, e.g. only the Duskwire passive was).
    fish: Optional[str] = None
    mutation: Optional[str] = None
    attributes: tuple = ()
    kg: Optional[float] = None


def parse_caught(text: str) -> Optional[dict]:
    """{"fish", "mutation", "attributes", "kg"} from the catch line, or None.
    The longest tail naming a known fishable is the fish; the words in
    front of it are the attributes and (at most one) mutation."""
    # "kg" is often read "k9" in the game font (recording, frame 975). The
    # coloured fish / mutation words read worst of all -- a dark "Fallen" was
    # never read at any scale -- so this is best effort; quest progress is
    # read from the quest tracker instead.
    m = re.search(r"caught\s+an?\s+(.+?)\s+at\s+([\d.,]+)\s*k[g9q]", text, flags=re.I)
    if not m:
        return None
    try:
        kg = float(m.group(2).replace(",", ""))
    except ValueError:
        kg = None
    # The fish is found in the fish index first, so a mutation word inside a
    # fish's own name ("Empyrean Relic") is not taken for its mutation.
    attrs, mutation, fish = split_catch(m.group(1))
    return {"fish": fish, "mutation": mutation, "attributes": attrs, "kg": kg}


def catch_notice(lines: list, rod: str) -> Optional[CatchNotice]:
    """Match complete notification phrases, joining OCR fragments on one row.

    Windows splits the coloured Duskwire sentence and sometimes omits the
    black words in the normal catch line. The catch line's fish, mutation and
    weight are read when it is there (mutation tally / quests, 2026-10-03).
    """
    rows: list[list] = []
    for text, box in sorted(lines, key=lambda item: (item[1][1], item[1][0])):
        for row in rows:
            top = row[0][1][1]
            height = max(1, row[0][1][3] - top)
            if abs(box[1] - top) <= max(3, height * .45):
                row.append((text, box))
                break
        else:
            rows.append([(text, box)])
    raw = [" ".join(text for text, _ in sorted(row, key=lambda item: item[1][0]))
           for row in rows]
    texts = [re.sub(r"[^a-z0-9]+", " ", t.lower()).strip() for t in raw]
    caught = next((parse_caught(r) for r, t in zip(raw, texts)
                   if re.search(r"\byou just caught\b", t)), None) or {}
    if rod.lower() == "duskwire":
        phrase = "chaotic energy surges through your strings"
        for text in texts:
            if (text.startswith("chaotic energy surges")
                    and SequenceMatcher(None, phrase, text).ratio() >= .82):
                return CatchNotice("duskwire passive", **caught)
    if any(re.search(r"\byou just caught\b", text) for text in texts):
        return CatchNotice("caught message", **caught)
    return None


def _read_lines(image: np.ndarray) -> list:
    # Import on the worker: initialising Windows OCR must not stall the servo.
    from fischocr import ocr_lines
    return ocr_lines(image, scale=min(2.0, 2200 / image.shape[1]))


class CatchWatch:
    INTERVAL_S = .25
    TIMEOUT_S = 1.5
    CLEAR_SAMPLES = 2

    def __init__(self, rod: str, reader: Callable = _read_lines):
        self.rod = rod
        self._reader = reader
        self._jobs: Queue = Queue(maxsize=1)
        self._results: Queue = Queue(maxsize=1)
        self._closed = Event()
        self._thread: Optional[Thread] = None
        self._epoch = 0
        self._busy_since: Optional[float] = None
        self._last_sample = -1e9
        self._clear_n = 0
        self._armed = False
        self.clear_ready = False
        self.confirmed: Optional[CatchNotice] = None
        self.error: Optional[str] = None

    def _work(self) -> None:
        while not self._closed.is_set():
            job = self._jobs.get()
            if job is None:
                return
            epoch, sampled_at, image = job
            try:
                notice = catch_notice(self._reader(image), self.rod)
                result = (epoch, sampled_at, notice, None)
            except Exception as exc:
                result = (epoch, sampled_at, None, type(exc).__name__)
            if self._closed.is_set():
                return
            self._results.put(result)
            del image, job

    def ready(self, now: float) -> bool:
        return (not self._closed.is_set() and self.error is None
                and self._busy_since is None
                and now - self._last_sample >= self.INTERVAL_S)

    def sample(self, image: np.ndarray, now: float) -> None:
        if not self.ready(now) or image.size == 0:
            return
        if self._thread is None:
            self._thread = Thread(target=self._work, name="fisch-catch-ocr", daemon=True)
            self._thread.start()
        self._busy_since = self._last_sample = now
        self._jobs.put_nowait((self._epoch, now, np.ascontiguousarray(image).copy()))

    def poll(self, now: float, active_since: Optional[float] = None) -> Optional[CatchNotice]:
        try:
            epoch, sampled_at, notice, error = self._results.get_nowait()
        except Empty:
            if self._busy_since is not None and now - self._busy_since > self.TIMEOUT_S:
                self.error = "OCR timeout"
            return self.confirmed
        self._busy_since = None
        if epoch != self._epoch:
            return self.confirmed
        if error:
            self.error = error
        elif notice is None:
            self._clear_n += 1
            if self._clear_n >= self.CLEAR_SAMPLES:
                self.clear_ready = True
                self._armed = True
        else:
            self._clear_n = 0
            self.clear_ready = False
            if (self._armed and active_since is not None
                    and sampled_at >= active_since and self.confirmed is None):
                self.confirmed = notice
            self._armed = False
        return self.confirmed

    def begin_attempt(self) -> None:
        self._epoch += 1
        self.confirmed = None
        self._armed = self.clear_ready

    def end_attempt(self) -> None:
        self._epoch += 1
        self.confirmed = None
        self._armed = False
        self._clear_n = 0
        self.clear_ready = False

    def close(self) -> None:
        self._closed.set()
        try:
            self._jobs.get_nowait()
        except Empty:
            pass
        self._jobs.put_nowait(None)
        # Never join a hung Windows OCR call. This daemon cannot hold up Stop.
        # Drain completed images/results; nothing is written to disk.
        try:
            self._results.get_nowait()
        except Empty:
            pass
