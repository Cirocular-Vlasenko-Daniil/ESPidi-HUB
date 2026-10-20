#!/usr/bin/env python3
"""Traces the icon engraved on the ESPidi board from a photo, for the drawing in web/board.js.

    python3 tools/trace_board_icon.py photo.jpg [--crop L,T,R,B] [--preview out.png]

--crop is the board's outline in the photo (default: the photo used for the site). The engraving
is bright on the black board: a top-hat filter drops the glow of the OLED, the lines are reduced
to the 288x176 drawing and thinned to one pixel. Prints ICON = {...} for web/board.js.
Needs Pillow, numpy and scikit-image."""
import argparse
import base64

import numpy as np
from PIL import Image, ImageChops, ImageFilter
from skimage.morphology import remove_small_objects, skeletonize

W, H = 288, 176
X0, Y0, X1, Y1 = 102, 7, 237, 161   # the icon's box in the drawing: halo and figure down to the keys
HALO = (165, 67, 60)


def trace(path: str, crop) -> np.ndarray:
    board = Image.open(path).convert("L").crop(crop)
    glow = board.filter(ImageFilter.MinFilter(11)).filter(ImageFilter.MaxFilter(11))
    lines = np.asarray(ImageChops.subtract(board, glow)) > 60
    small = np.asarray(Image.fromarray(lines.astype(np.uint8) * 255).resize((W, H), Image.BOX)) > 0.25 * 255
    bits = remove_small_objects(skeletonize(small), min_size=8, connectivity=2)
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy, r = HALO
    inside = ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) | ((yy >= cy) & (xx >= X0))
    return bits & inside & (xx >= X0) & (xx < X1) & (yy >= Y0) & (yy < Y1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("photo")
    ap.add_argument("--crop", default="215,365,1645,1240")
    ap.add_argument("--preview")
    args = ap.parse_args()
    bits = trace(args.photo, tuple(int(v) for v in args.crop.split(",")))
    box = bits[Y0:Y1, X0:X1]
    packed = np.packbits(box, axis=None)
    print(f'const ICON = {{ x: {X0}, y: {Y0}, w: {X1 - X0}, h: {Y1 - Y0}, bits: "{base64.b64encode(packed.tobytes()).decode()}" }};')
    if args.preview:
        Image.fromarray(bits.astype(np.uint8) * 255).resize((W * 3, H * 3), Image.NEAREST).save(args.preview)


if __name__ == "__main__":
    main()
