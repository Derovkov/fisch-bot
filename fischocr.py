"""
Text recognition with the OCR engine built into Windows 10/11 (no extra install
beyond the small winrt-* Python bindings).

    lines = ocr_lines(rgb_image)   # [(text, (x0, y0, x1, y1)), ...]

Used to read the in-game rod screen (names, enchants, Equip/Equipped). The game's
outlined font reads better enlarged, so images are upscaled before recognition.
"""
from __future__ import annotations

import asyncio
import difflib
import re
from typing import Iterable, Optional

import cv2
import numpy as np
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

_engine: Optional[OcrEngine] = None


def _get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        _engine = OcrEngine.try_create_from_user_profile_languages()
        if _engine is None:
            raise RuntimeError("Windows OCR is not available (no OCR language installed)")
    return _engine


def _bitmap(rgb: np.ndarray) -> SoftwareBitmap:
    h, w = rgb.shape[:2]
    bgra = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGRA)
    dw = DataWriter()
    dw.write_bytes(bgra.tobytes())
    # The 4-argument overload: this binding rejects the alpha-mode variant.
    return SoftwareBitmap.create_copy_from_buffer(
        dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)


async def _recognize(bmp: SoftwareBitmap):
    return await _get_engine().recognize_async(bmp)


def ocr_lines(rgb: np.ndarray, scale: float = 2.0) -> list[tuple[str, tuple[int, int, int, int]]]:
    """OCR an RGB image; returns lines with boxes in the ORIGINAL image's pixels."""
    img = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) \
        if scale != 1 else rgb
    res = asyncio.run(_recognize(_bitmap(img)))
    out = []
    for line in res.lines:
        xs, ys, xe, ye = [], [], [], []
        for w in line.words:
            r = w.bounding_rect
            xs.append(r.x); ys.append(r.y); xe.append(r.x + r.width); ye.append(r.y + r.height)
        if xs:
            box = tuple(int(v / scale) for v in (min(xs), min(ys), max(xe), max(ye)))
            out.append((line.text, box))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9']+", " ", s.lower()).strip()


def best_match(text: str, names: Iterable[str], cutoff: float = 0.8) -> Optional[str]:
    """Closest known name to an OCR'd string (OCR drops/garbles a letter or two)."""
    names = list(names)
    by_norm = {_norm(n): n for n in names}
    hit = difflib.get_close_matches(_norm(text), list(by_norm), n=1, cutoff=cutoff)
    return by_norm[hit[0]] if hit else None
