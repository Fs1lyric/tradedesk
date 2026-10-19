"""Draw the toolbar icons: a blue rounded square with a rising line. Run from extension/."""

import struct
import zlib

BLUE, WHITE = (47, 111, 222), (255, 255, 255)
LINE = [(0.2, 0.68), (0.42, 0.48), (0.58, 0.6), (0.8, 0.3)]


def seg_dist(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def pixel(x, y, n, ss=4):
    """Supersampled RGBA at pixel (x, y) of an n x n icon."""
    r = 0.22 * n
    width = max(1.6, 0.09 * n) / 2
    cov = ink = 0
    for i in range(ss):
        for j in range(ss):
            px, py = x + (i + 0.5) / ss, y + (j + 0.5) / ss
            cx, cy = min(max(px, r), n - r), min(max(py, r), n - r)
            if (px - cx) ** 2 + (py - cy) ** 2 > r * r:
                continue
            cov += 1
            pts = [(u * n, v * n) for u, v in LINE]
            if min(seg_dist(px, py, a, b) for a, b in zip(pts, pts[1:])) <= width:
                ink += 1
    if not cov:
        return (0, 0, 0, 0)
    f = ink / cov
    rgb = tuple(round(b * (1 - f) + w * f) for b, w in zip(BLUE, WHITE))
    return (*rgb, round(255 * cov / ss**2))


def png(n):
    rows = b"".join(b"\0" + bytes(c for x in range(n) for c in pixel(x, y, n)) for y in range(n))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


for size in (16, 32, 48, 128):
    with open(f"icons/{size}.png", "wb") as f:
        f.write(png(size))
