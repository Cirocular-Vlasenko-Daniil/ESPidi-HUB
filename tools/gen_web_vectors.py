#!/usr/bin/env python3
"""Writes web/test-vectors.json: what tools/espidi_hub.py produces, for web/test.html to compare
against web/espidi.js. Run after changing either side."""
import hashlib
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import espidi_hub as hub  # noqa: E402


def lcg(n: int, seed: int) -> bytes:
    """Same generator as lcg() in web/test.html."""
    x, out = seed, bytearray(n)
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def catalog_file(name: str) -> bytes:
    with open(os.path.join(ROOT, "web", "catalog", name), "rb") as f:
        return f.read()


def main() -> None:
    v = {"hashes": [], "layouts": [], "meta": [], "images": [], "covers": {}, "merged": None}

    for inp in ({"text": ""}, {"text": "abc"}, {"lcg": [1000, 1]}, {"lcg": [64, 7]}):
        data = inp["text"].encode() if "text" in inp else lcg(*inp["lcg"])
        v["hashes"].append({"input": inp, "md5": md5(data), "crc32": zlib.crc32(data)})

    for sizes in ([0, 0], [430_000, 0], [430_000, 1_700_000],
                  [hub.APP_END - hub.APP_START - hub.PLACEHOLDER, 0], [0, 2_000_000], [65_536, 65_537]):
        lay = hub.empty_layout()
        for i, s in enumerate(sizes):
            lay = lay.with_slot(i, s)
        v["layouts"].append({"sizes": sizes, "slot": lay.slot, "offsets": [lay.offset(0), lay.offset(1)],
                             "max": [lay.max_size(0), lay.max_size(1)], "free": lay.free(),
                             "table_md5": md5(hub.build_table(lay.partitions()))})

    metas = [hub.SlotMeta("redsea", "RED SEA", "1.1 beta", "Daniil Vlasenko", 431488, 0xB596FDF7),
             hub.SlotMeta("x" * 20, "N" * 30, "1.0.0-very-long-version", "", 1, 2)]
    v["meta"].append({"slots": [m.__dict__ for m in metas], "md5": md5(hub.pack_meta(metas))})
    v["meta"].append({"slots": [hub.SlotMeta().__dict__] * 2, "md5": md5(hub.pack_meta([hub.SlotMeta()] * 2))})

    for name in ("redsea-1.0.bin", "redsea-1.1-beta.bin", "template-0.1.0.bin"):
        info = hub.inspect_image(catalog_file(name))
        v["images"].append({"file": name, "chip_id": info.chip_id, "project": info.project,
                            "app_version": info.app_version, "idf_version": info.idf_version,
                            "desc": ({k: getattr(info.desc, k) for k in ("id", "name", "version", "author")}
                                     if info.desc else None),
                            "cover": info.cover.hex() if info.cover else None,
                            "problems": len(info.problems)})

    # Covers: the block format, and pixels_to_cover() on RGBA input the browser gets from a canvas.
    def rgba_rows(w, h, seed):
        raw = lcg(w * h * 4, seed)
        return [[((raw[o] * 299 + raw[o + 1] * 587 + raw[o + 2] * 114) // 1000, raw[o + 3])
                 for o in range((y * w) * 4, (y * w + w) * 4, 4)] for y in range(h)]
    cv = v["covers"]
    cv["pixels"] = [{"size": [w, h], "seed": seed, "invert": inv,
                     "cover": hub.pixels_to_cover(w, h, rgba_rows(w, h, seed), inv).hex()}
                    for w, h, seed, inv in ((64, 32, 4, False), (128, 64, 5, False), (192, 96, 6, True))]
    cv["block"] = {"bits": [256, 1], "md5": md5(hub.pack_cover(lcg(256, 1)))}
    cv["catalog"] = {name: hub.load_cover(os.path.join(ROOT, "web", "catalog", name)).hex()
                     for name in ("redsea.cover.png", "redsea-beta.cover.png", "template.cover.png")}
    with_covers = [hub.SlotMeta("redsea", "RED SEA", "1.0", "Daniil Vlasenko", 419984, 1), hub.SlotMeta()]
    cv["meta"] = {"slots": [m.__dict__ for m in with_covers], "covers": [lcg(256, 8).hex(), None],
                  "md5": md5(hub.pack_meta(with_covers, [lcg(256, 8), None]))}

    s1, s2 = catalog_file("redsea-1.1-beta.bin"), catalog_file("template-0.1.0.bin")
    mm = [hub.SlotMeta("redsea", "RED SEA", "1.1 beta", "Daniil Vlasenko", len(s1), zlib.crc32(s1)),
          hub.SlotMeta("template", "TEMPLATE", "0.1.0", "ESPidi HUB", len(s2), zlib.crc32(s2))]
    covers = [hub.load_cover(os.path.join(ROOT, "web", "catalog", "redsea-beta.cover.png")), hub.inspect_image(s2).cover]
    image = hub.build_image(lcg(20000, 2), lcg(150000, 3), [s1, s2], mm, covers)
    v["merged"] = {"bootloader": [20000, 2], "hub": [150000, 3], "slots": ["redsea-1.1-beta.bin", "template-0.1.0.bin"],
                   "metas": [m.__dict__ for m in mm], "covers": [c.hex() for c in covers],
                   "size": len(image), "md5": md5(image)}

    with open(os.path.join(ROOT, "web", "test-vectors.json"), "w") as f:
        json.dump(v, f, indent=1)
        f.write("\n")
    print("wrote web/test-vectors.json")


if __name__ == "__main__":
    main()
