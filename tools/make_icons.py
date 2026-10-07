#!/usr/bin/env python3
"""Draw the Prism app icons (run once, or again if you change the design).

The mark is a triangle cut into three slices, one each for left, centre and right.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "icons"
OUT.mkdir(exist_ok=True)

INK = (21, 23, 29)
LEFT, CENTRE, RIGHT = (96, 132, 255), (160, 167, 182), (255, 110, 70)

# The three slices on a 32 x 32 grid (same shapes as the logo in index.html)
SLICES = [
    (LEFT, [(4, 27), (10.93, 27), (10.93, 13.72)]),
    (CENTRE, [(12.53, 27), (19.47, 27), (19.47, 10.65), (16, 4), (12.53, 10.65)]),
    (RIGHT, [(28, 27), (21.07, 27), (21.07, 13.72)]),
]


def draw(size, glyph_scale):
    k = 4  # supersample for smooth edges
    s = size * k
    im = Image.new("RGB", (s, s), INK)
    d = ImageDraw.Draw(im)
    unit = s / 32 * glyph_scale  # pixels per grid unit
    cx, cy = 16, 15.5            # glyph centre on the grid
    for colour, pts in SLICES:
        d.polygon([(s / 2 + (x - cx) * unit, s / 2 + (y - cy) * unit) for x, y in pts], fill=colour)
    return im.resize((size, size), Image.LANCZOS)


draw(512, 0.62).save(OUT / "icon-512.png")
draw(192, 0.62).save(OUT / "icon-192.png")
draw(180, 0.62).save(OUT / "apple-touch-icon.png")
draw(512, 0.46).save(OUT / "icon-maskable-512.png")
print("icons written to", OUT)
