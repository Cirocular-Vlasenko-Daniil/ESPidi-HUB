#!/usr/bin/env python3
"""Fills size and sha256 of every firmware in web/catalog/catalog.json and checks each image
and cover (optional "cover": a PNG or PBM next to the image, 64x32 or a multiple of it)."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import espidi_hub as hub  # noqa: E402

CATALOG = os.path.join(os.path.dirname(HERE), "web", "catalog", "catalog.json")


def main() -> int:
    with open(CATALOG) as f:
        cat = json.load(f)
    failed = False
    for fw in cat["firmwares"]:
        path = os.path.join(os.path.dirname(CATALOG), fw["file"])
        with open(path, "rb") as f:
            data = f.read()
        info = hub.inspect_image(data)
        fw["size"] = len(data)
        fw["sha256"] = hashlib.sha256(data).hexdigest()
        problems = list(info.problems)
        if fw.get("cover"):
            try:
                hub.load_cover(os.path.join(os.path.dirname(CATALOG), fw["cover"]))
            except (OSError, ValueError) as e:
                problems.append(f"cover {fw['cover']}: {e}")
        status = "; ".join(problems) or ("ok, cover" if fw.get("cover") or info.cover else "ok")
        failed |= bool(problems)
        print(f"{fw['file']}: {hub.fmt_size(len(data))}, {status}")
    with open(CATALOG, "w") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
