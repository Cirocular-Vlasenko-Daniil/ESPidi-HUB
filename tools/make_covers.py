#!/usr/bin/env python3
"""Draws the catalog covers (web/catalog/*.cover.png) and the template's cover.png + cover.h.

Covers are 64x32, 1 bit: a 1-bit grayscale PNG row is exactly a cover row (8 bytes, MSB first,
1 = white = lit), so the PNG and ESPIDI_COVER() hold the same bytes."""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import espidi_hub as hub  # noqa: E402

W, H = hub.COVER_W, hub.COVER_H


def hub_font():
    with open(os.path.join(ROOT, "hub", "main", "font.c")) as f:
        src = f.read()
    glyphs = {}
    for m in re.finditer(r"\{'(.)',\s*\{((?:\s*\"[.X]{5}\",?){7})\s*\}\}", src):
        glyphs[m.group(1)] = re.findall(r"\"([.X]{5})\"", m.group(2))
    return glyphs


FONT = hub_font()

# RED SEA's sailboat sprite (REDSEA.ino, sailboat[9][8]).
SAILBOAT = ["00000000", "00011000", "00011100", "00111100", "01011100",
            "01011001", "11111111", "11111110", "01111100"]


class Canvas:
    def __init__(self):
        self.px = [[False] * W for _ in range(H)]

    def set(self, x, y, on=True):
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = on

    def rect(self, x, y, w, h, fill=False, on=True):
        for j in range(y, y + h):
            for i in range(x, x + w):
                if fill or i in (x, x + w - 1) or j in (y, y + h - 1):
                    self.set(i, j, on)

    def text(self, x, y, s, scale=1, on=True):
        for ch in s.upper():
            rows = FONT.get(ch, FONT["?"])
            for r, row in enumerate(rows):
                for c, p in enumerate(row):
                    if p == "X":
                        self.rect(x + c * scale, y + r * scale, scale, scale, fill=True, on=on)
            x += 6 * scale

    def sprite(self, x, y, rows, scale=1):
        for r, row in enumerate(rows):
            for c, p in enumerate(row):
                if p == "1":
                    self.rect(x + c * scale, y + r * scale, scale, scale, fill=True)

    def bits(self) -> bytes:
        out = bytearray(hub.COVER_BYTES)
        for y in range(H):
            for x in range(W):
                if self.px[y][x]:
                    out[y * 8 + x // 8] |= 0x80 >> (x % 8)
        return bytes(out)


def write_png(path, bits):
    with open(path, "wb") as f:
        f.write(hub.cover_png(bits))


def disc(c, cx, cy, r, on=True):
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r:
                c.set(x, y, on)


# The HUB prints the name and version next to the cover, so covers are pictures, not titles.
def red_sea(beta=False):
    c = Canvas()
    if beta:                                          # the beta has a BETA tag where the sun is
        c.rect(0, 1, 25, 9, fill=True)
        c.text(1, 2, "BETA", on=False)
    else:                                             # sun with rays
        disc(c, 10, 9, 5)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            for k in (7, 8):
                c.set(10 + dx * k, 9 + dy * k)
    c.sprite(24, 0, SAILBOAT, scale=3)                # RED SEA's sailboat, x3
    for x in range(W):   # sea: a solid wave and a dithered one, as in RED SEA's background
        y = 26 + round(math.sin(x * 0.33) * 1.3)
        c.rect(x, y, 1, H - y, fill=True, on=False)
        c.set(x, y)
        if x % 2 == 0:
            c.set(x, 30 + round(math.cos(x * 0.27 + 1) * 1.0))
    return c.bits()


def template():
    c = Canvas()
    c.rect(18, 3, 28, 26)                             # a chip with the SDK inside
    c.rect(21, 6, 3, 3, fill=True)
    for y in range(6, 28, 4):
        c.rect(14, y, 4, 2, fill=True)
        c.rect(46, y, 4, 2, fill=True)
    c.text(23, 13, "SDK")
    return c.bits()


def main():
    catalog = os.path.join(ROOT, "web", "catalog")
    template_repo = os.path.join(ROOT, "templates", "arduino-firmware")
    # RED SEA's covers live in the catalog until its repository has an espidi.json; the
    # template is an ESPidi-ready repository, so its cover.png is the source and cover.h follows it.
    covers = {os.path.join(catalog, "redsea.cover.png"): red_sea(),
              os.path.join(catalog, "redsea-beta.cover.png"): red_sea(beta=True),
              os.path.join(template_repo, "cover.png"): template()}
    for path, bits in covers.items():
        write_png(path, bits)
        assert hub.load_cover(path) == bits
        print(f"{os.path.relpath(path, ROOT)}\n{hub.cover_ascii(bits)}\n")
    with open(os.path.join(template_repo, hub.MANIFEST)) as f:
        manifest = json.load(f)
    print(hub.sync_cover(template_repo, manifest, hub.load_cover(os.path.join(template_repo, manifest["cover"]))))


if __name__ == "__main__":
    main()
