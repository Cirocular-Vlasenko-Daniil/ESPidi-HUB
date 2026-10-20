#!/usr/bin/env python3
"""Draws the link previews (Open Graph images, 1200x630) of the composer site:
web/og.png for the composer and web/og-espidi.png for the ESPidi page.

    python3 tools/make_og_image.py

The board comes from web/board.js, run by macOS's JavaScriptCore shell (jsc); the text is the
HUB's 5x7 font from web/font.js. Every pixel is a whole square, like on the OLED. Needs Pillow."""
import glob
import json
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
W, H = 1200, 630
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def glyphs() -> dict:
    with open(os.path.join(WEB, "font.js")) as f:
        s = f.read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def board() -> Image.Image:
    """The board drawing of web/board.js, one pixel per pixel."""
    jsc = JSC if os.path.exists(JSC) else (glob.glob("/System/Library/Frameworks/JavaScriptCore.framework/Versions/*/Helpers/jsc") or ["jsc"])[0]
    with tempfile.TemporaryDirectory() as tmp:
        main = os.path.join(tmp, "main.mjs")
        with open(main, "w") as f:
            f.write(f'import {{ boardPixels, BOARD_W, BOARD_H }} from "{os.path.join(WEB, "board.js")}";\n'
                    'print(BOARD_W + " " + BOARD_H + " " + Array.from(boardPixels(), (v) => (v ? "1" : "0")).join(""));\n')
        out = subprocess.run([jsc, "-m", main], capture_output=True, text=True, check=True).stdout.split()
    w, h, bits = int(out[0]), int(out[1]), out[2]
    img = Image.new("1", (w, h), 0)
    img.putdata([255 if b == "1" else 0 for b in bits])
    return img


class Canvas:
    def __init__(self):
        self.img = Image.new("1", (W, H), 0)
        self.draw = ImageDraw.Draw(self.img)
        self.font = glyphs()

    def text(self, x, y, s, scale):
        for ch in s.upper():
            for r, row in enumerate(self.font.get(ch, self.font["?"])):
                for c in range(5):
                    if row & (0x10 >> c):
                        self.draw.rectangle([x + c * scale, y + r * scale, x + (c + 1) * scale - 1, y + (r + 1) * scale - 1], fill=1)
            x += 6 * scale

    @staticmethod
    def width(s, scale):
        return (len(s) * 6 - 1) * scale

    def paste(self, img, x, y, scale):
        self.img.paste(img.resize((img.width * scale, img.height * scale), Image.NEAREST), (x, y))

    def frame(self, x, y, w, h, line):
        for i in range(line):
            self.draw.rectangle([x + i, y + i, x + w - 1 - i, y + h - 1 - i], outline=1)


def menu_screen() -> Image.Image:
    """The HUB menu with two firmwares, as draw_menu() in hub/main/main.c shows it."""
    c = Canvas()
    oled = Image.new("1", (128, 32), 0)
    c.img, c.draw = oled, ImageDraw.Draw(oled)
    c.text(0, 0, "ESPIDI HUB 0.1.0", 1)
    c.text(0, 8, ">1 PLAY RED SEA", 1)
    c.text(0, 16, " 2 PAGE TEMPLATE", 1)
    c.text(0, 24, "FREE 1.8MB ENC=RUN", 1)
    return oled


def composer(b: Image.Image) -> Image.Image:
    c = Canvas()
    c.text(60, 60, "ESPIDI HUB", 10)
    c.text(64, 168, "TWO FIRMWARES ON ONE ESPIDI", 4)
    x, y, s = 60, 268, 3
    c.frame(x, y, 128 * s + 2 * 12, 32 * s + 2 * 12, s)
    c.paste(menu_screen(), x + 12, y + 12, s)
    c.text(64, 560, "ESPIDI-HUB.GITHUB.IO", 3)
    c.paste(b, W - b.width * 2 - 50, H - b.height * 2 - 46, 2)
    return c.img


def espidi_page(b: Image.Image) -> Image.Image:
    c = Canvas()
    c.paste(b, (W - b.width * 3) // 2, (H - b.height * 3) // 2, 3)
    return c.img


def main() -> None:
    b = board()
    for name, img in (("og.png", composer(b)), ("og-espidi.png", espidi_page(b))):
        path = os.path.join(WEB, name)
        img.save(path, optimize=True)
        print(f"{path}: {img.width}x{img.height}, {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
