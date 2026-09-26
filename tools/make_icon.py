#!/usr/bin/env python3
"""Draw the app icon to KidsDictionary/Assets.xcassets/AppIcon.appiconset/AppIcon.png.

Pure Python (zlib + struct): a 1024x1024 RGB PNG with no alpha, as App Store
Connect requires. A cream book page on the tap-blue background, with lines of
text and one word marked with the yellow highlighter. Shapes are signed-distance
fields, so edges are anti-aliased. Run once and commit the PNG.
"""
import math, struct, zlib
from pathlib import Path

SIZE = 1024
OUT = (Path(__file__).resolve().parent.parent / "KidsDictionary" / "Assets.xcassets"
       / "AppIcon.appiconset" / "AppIcon.png")

BG_TOP, BG_BOTTOM = (62, 110, 230), (36, 72, 178)     # around the tap blue #2F5BD3
PAGE = (255, 248, 236)                                  # cream #FFF8EC
INK = (31, 42, 68)                                      # #1F2A44
LINE = (196, 190, 176)
HIGHLIGHT = (255, 209, 102)                             # #FFD166

LEFT, RIGHT, TOP, BOTTOM, CORNER = 222, 802, 196, 828, 44
LINE_R = 17
LINES = [(292, 732, 330), (292, 690, 418), (292, 732, 594), (292, 610, 682)]
WORD = (292, 560, 506)            # the highlighted word's line
HL = (272, 580, 470, 542)         # highlighter box x0, x1, y0, y1
HL_CORNER = 16


def clamp01(v):
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def coverage(d):
    return clamp01(0.5 - d)


def mix(c, p, a):
    return tuple(c[i] + (p[i] - c[i]) * a for i in range(3))


def box_sdf(px, py, x0, x1, y0, y1, r):
    hx, hy = (x1 - x0) / 2, (y1 - y0) / 2
    qx = abs(px - (x0 + hx)) - (hx - r)
    qy = abs(py - (y0 + hy)) - (hy - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def seg(px, py, x0, x1, y):
    t = clamp01((px - x0) / (x1 - x0))
    return math.hypot(px - x0 - t * (x1 - x0), py - y)


def render():
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)
        py = y + 0.5
        for x in range(SIZE):
            px = x + 0.5
            c = mix(BG_TOP, BG_BOTTOM, clamp01((0.3 * px + py) / (1.3 * SIZE)))
            ds = box_sdf(px, py - 22, LEFT, RIGHT, TOP, BOTTOM, CORNER)
            t = clamp01((ds + 10) / 46)
            c = mix(c, (0, 0, 0), 0.28 * (1 - t * t * (3 - 2 * t)))
            a = coverage(box_sdf(px, py, LEFT, RIGHT, TOP, BOTTOM, CORNER))
            if a > 0:
                c = mix(c, PAGE, a)
                c = mix(c, HIGHLIGHT, coverage(box_sdf(px, py, *HL, HL_CORNER)))
                for x0, x1, ly in LINES:
                    if abs(py - ly) <= LINE_R + 1:
                        c = mix(c, LINE, coverage(seg(px, py, x0, x1, ly) - LINE_R))
                if abs(py - WORD[2]) <= LINE_R + 1:
                    c = mix(c, INK, coverage(seg(px, py, WORD[0], WORD[1], WORD[2]) - LINE_R))
            raw.extend(min(255, max(0, int(v + 0.5))) for v in c)
    return bytes(raw)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def main():
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(), 9)) + chunk(b"IEND", b"")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(png)
    print(f"Wrote {SIZE}x{SIZE} RGB icon to {OUT}")


if __name__ == "__main__":
    main()
