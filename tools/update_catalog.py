#!/usr/bin/env python3
"""Refreshes web/catalog/catalog.json: checks every firmware and cover, fills size and sha256.

An entry with "source" — an ESPidi-ready repository, owner/repo[/folder][@ref] — is first
synced from it: the firmware, the cover, name, version, author and summary come from the
repository and are copied next to the catalog. The site serves those copies itself (GitHub
can be slow or blocked for some visitors); the GitHub tab of the site reads repositories live.
--offline skips the sync."""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import espidi_hub as hub  # noqa: E402

CATALOG = os.path.join(os.path.dirname(HERE), "web", "catalog", "catalog.json")
DIR = os.path.dirname(CATALOG)


def write(name: str, data: bytes) -> None:
    with open(os.path.join(DIR, name), "wb") as f:
        f.write(data)


def sync(fw: dict, others: set) -> str:
    src = hub.open_repo(fw["source"])
    rep = hub.check_repo(src)
    if rep.problems:
        raise hub.RepoError("; ".join(rep.problems))
    d, m = rep.meta(), rep.manifest
    stem = f"{d.id or fw['id']}-{re.sub(r'[^A-Za-z0-9._-]+', '-', d.version or 'latest')}"
    old = {fw.get("file"), fw.get("cover")}
    fw.update(id=d.id or fw["id"], name=d.name, version=d.version, author=d.author, file=f"{stem}.bin")
    if m.get("summary"):
        fw["summary"] = m["summary"] if isinstance(m["summary"], dict) else {"en": m["summary"]}
    write(fw["file"], rep.firmware)
    if rep.cover:
        fw["cover"] = f"{stem}.cover.png"
        write(fw["cover"], hub.cover_png(rep.cover))
    else:
        fw.pop("cover", None)
    fw["commit"] = getattr(src, "sha", None) or ""
    for name in old - {fw["file"], fw.get("cover")} - others - {None}:   # files of the previous version
        if os.path.exists(os.path.join(DIR, name)):
            os.remove(os.path.join(DIR, name))
    return f"synced from {src.where}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="don't sync entries with a source")
    args = ap.parse_args()
    with open(CATALOG) as f:
        cat = json.load(f)
    failed = False
    for fw in cat["firmwares"]:
        notes = []
        if fw.get("source") and not args.offline:
            others = {x.get(k) for x in cat["firmwares"] if x is not fw for k in ("file", "cover", "screen")}
            try:
                notes.append(sync(fw, others))
            except (hub.RepoError, OSError) as e:
                notes.append(f"ERROR sync {fw['source']}: {e}")
                failed = True
        with open(os.path.join(DIR, fw["file"]), "rb") as f:
            data = f.read()
        info = hub.inspect_image(data)
        fw["size"] = len(data)
        fw["sha256"] = hashlib.sha256(data).hexdigest()
        problems = list(info.problems)
        if fw.get("cover"):
            try:
                hub.load_cover(os.path.join(DIR, fw["cover"]))
            except (OSError, ValueError) as e:
                problems.append(f"cover {fw['cover']}: {e}")
        failed |= bool(problems)
        status = "; ".join(problems) or ("ok, cover" if fw.get("cover") or info.cover else "ok")
        print(f"{fw['file']}: {hub.fmt_size(len(data))}, {status}" + "".join(f"\n  {n}" for n in notes))
    with open(CATALOG, "w") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
