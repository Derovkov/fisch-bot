"""Draws the app icon (ui/icons/app/fischbot.ico + .png): the sidebar logo --
a white fish on the blue -> cyan gradient rounded square (index.html .logo).
Run again after changing it: python dev_tests/make_icon.py"""
from pathlib import Path
import struct

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "ui" / "icons" / "app"
BIG = 1024


def bezier(p0, p1, p2, p3, n=40):
    t = np.linspace(0, 1, n)[:, None]
    return ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3)


def fish_body():
    """The logo's SVG path (24x24 view box), as points."""
    P = lambda x, y: np.array([x, y], float)
    pts = [bezier(P(16.5, 12), P(14.7, 15.2), P(11.5, 17), P(8, 17)),
           bezier(P(8, 17), P(6.4, 17), P(5, 16.5), P(4, 15.8)),
           bezier(P(4, 15.8), P(5, 13.9), P(6.5, 12), P(6.5, 12)),
           bezier(P(6.5, 12), P(8, 10.1), P(5, 10.1), P(4, 8.2)),   # S: reflected control
           bezier(P(4, 8.2), P(5, 7.5), P(6.4, 7), P(8, 7)),
           bezier(P(8, 7), P(11.5, 7), P(14.7, 8.8), P(16.5, 12))]
    return np.vstack(pts)


def render(size=BIG) -> np.ndarray:
    """RGBA, drawn big and shrunk for smooth edges."""
    x = np.linspace(0, 1, size)[None, :]
    a, b = np.array([0x4a, 0x8d, 0xff], float), np.array([0x36, 0xc5, 0xff], float)
    grad = (a + (b - a) * x[..., None]).repeat(size, 0)          # left -> right, like --grad
    rgb = np.ascontiguousarray(grad.astype(np.uint8))
    # rounded square mask
    mask = np.zeros((size, size), np.uint8)
    r = int(size * 0.24)
    cv2.rectangle(mask, (r, 0), (size - r, size), 255, -1)
    cv2.rectangle(mask, (0, r), (size, size - r), 255, -1)
    for cx, cy in ((r, r), (size - r, r), (r, size - r), (size - r, size - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    # fish: centred, ~74% of the width
    s = 0.74 * size / 17.0
    off = np.array([size / 2 - 12.5 * s, size / 2 - 12 * s])
    body = (fish_body() * s + off).astype(np.int32)
    tail = (np.array([[16.5, 12], [21, 8.5], [21, 15.5]]) * s + off).astype(np.int32)
    white = (255, 255, 255)
    cv2.fillPoly(rgb, [body], white, cv2.LINE_AA)
    cv2.fillPoly(rgb, [tail], white, cv2.LINE_AA)
    # join tail and body with a rounded stroke like the SVG's
    cv2.polylines(rgb, [body, tail], True, white, max(1, int(s * 1.2)), cv2.LINE_AA)
    eye = (np.array([8.6, 10.9]) * s + off).astype(int)
    cv2.circle(rgb, tuple(int(v) for v in eye), int(s * 0.95), (0x2f, 0x6f, 0xe0), -1, cv2.LINE_AA)
    return np.dstack([rgb, mask])


def write_ico(path: Path, images: dict) -> None:
    """ICO with PNG-compressed entries (Windows Vista+)."""
    blobs = []
    for size, rgba in sorted(images.items()):
        ok, png = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        blobs.append((size, png.tobytes()))
    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for size, png in blobs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(head + entries + data)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = render()
    sizes = {n: cv2.resize(big, (n, n), interpolation=cv2.INTER_AREA) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)}
    write_ico(OUT / "fischbot.ico", sizes)
    cv2.imwrite(str(OUT / "fischbot.png"), cv2.cvtColor(sizes[256], cv2.COLOR_RGBA2BGRA))
    print("wrote", OUT / "fischbot.ico", (OUT / "fischbot.ico").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
