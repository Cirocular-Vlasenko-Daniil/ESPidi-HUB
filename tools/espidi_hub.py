#!/usr/bin/env python3
"""ESPidi HUB installer: puts firmwares into the two slots and sizes the slots itself.

Flash layout (4 MB) — see docs/ARCHITECTURE.md:

    0x000000  bootloader (ESPidi HUB)
    0x008000  partition table          <- rewritten by this tool on every install
    0x009000  nvs      16 KB           settings of all firmwares, never erased here
    0x00D000  espidi    4 KB           what is in each slot (name, version, …)
    0x00E000  otadata   8 KB           which slot starts after a software reset
    0x010000  hub     256 KB           the HUB app
    0x050000  slot1   ->               grows up from here
              (free)
           <- slot2   0x300000         grows down to here
    0x300000  spiffs    1 MB           shared LittleFS
    0x400000

Slot 1 always starts at 0x50000 and slot 2 always ends at 0x300000, so installing a
firmware never moves the other one: the tool only checks that the free gap between
them is big enough and rewrites the two partition table entries.

Commands (run with -h for options):
    check FILE              inspect a firmware .bin
    plan [--slot1 F] [--slot2 F]
                            show the layout two firmwares would get (offline)
    table --out FILE        write the empty partition table (what CI compares)
    image --hub-dir D [--slot1 F] [--slot2 F] --out FILE
                            one fresh-install image (HUB + firmwares) to flash at 0x0
    info -p PORT            what is installed on a device
    base -p PORT            first install: erase flash, write bootloader + HUB
    install -p PORT --slot N FILE
    remove -p PORT --slot N

Needs esptool (`pip install esptool`) for the device commands.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
FLASH_SIZE = 0x400000
BOOTLOADER_OFFSET = 0x0
PT_OFFSET = 0x8000
PT_MAX = 0xC00
NVS_OFFSET, NVS_SIZE = 0x9000, 0x4000
META_OFFSET, META_SIZE = 0xD000, 0x1000
OTADATA_OFFSET, OTADATA_SIZE = 0xE000, 0x2000
HUB_OFFSET, HUB_SIZE = 0x10000, 0x40000
APP_START = HUB_OFFSET + HUB_SIZE   # 0x50000
APP_END = 0x300000
FS_OFFSET, FS_SIZE = APP_END, FLASH_SIZE - APP_END
APP_ALIGN = 0x10000
PLACEHOLDER = 0x10000               # an empty slot keeps a 64 KB entry at its edge
SLOTS = 2
SLOT_BUTTON = {1: "PLAY", 2: "L/R"}

TYPE_APP, TYPE_DATA, TYPE_ESPIDI = 0x00, 0x01, 0x40
SUB_FACTORY, SUB_OTA0, SUB_OTA1 = 0x00, 0x10, 0x11
SUB_OTA, SUB_NVS, SUB_SPIFFS = 0x00, 0x02, 0x82

RELEASES = "https://github.com/Cirocular-Vlasenko-Daniil/ESPidi-HUB/releases"


def align_up(n: int, a: int = APP_ALIGN) -> int:
    return (n + a - 1) // a * a


def fmt_size(n: int) -> str:
    """Rounded down (as fmt() in web/app.js): free space is never overstated."""
    return f"{n // 1024} KB" if n < 1024 * 1024 else f"{n * 100 // (1024 * 1024) / 100:.2f} MB"


@dataclass
class Partition:
    name: str
    type: int
    subtype: int
    offset: int
    size: int
    flags: int = 0


@dataclass
class Layout:
    """Space given to each slot; 0 = empty (keeps a placeholder entry)."""
    slot: List[int] = field(default_factory=lambda: [0, 0])

    def alloc(self, i: int) -> int:
        return self.slot[i] or PLACEHOLDER

    def offset(self, i: int) -> int:
        return APP_START if i == 0 else APP_END - self.alloc(1)

    def max_size(self, i: int) -> int:
        """Largest allocation slot i can get without touching the other slot."""
        return (APP_END - APP_START) - self.alloc(1 - i)

    def free(self) -> int:
        return (APP_END - APP_START) - self.slot[0] - self.slot[1]

    def partitions(self) -> List[Partition]:
        return [
            Partition("nvs", TYPE_DATA, SUB_NVS, NVS_OFFSET, NVS_SIZE),
            Partition("espidi", TYPE_ESPIDI, 0x00, META_OFFSET, META_SIZE),
            Partition("otadata", TYPE_DATA, SUB_OTA, OTADATA_OFFSET, OTADATA_SIZE),
            Partition("hub", TYPE_APP, SUB_FACTORY, HUB_OFFSET, HUB_SIZE),
            Partition("slot1", TYPE_APP, SUB_OTA0, self.offset(0), self.alloc(0)),
            Partition("slot2", TYPE_APP, SUB_OTA1, self.offset(1), self.alloc(1)),
            Partition("spiffs", TYPE_DATA, SUB_SPIFFS, FS_OFFSET, FS_SIZE),
        ]

    def with_slot(self, i: int, image_size: int) -> "Layout":
        need = align_up(image_size) if image_size else 0
        if need > self.max_size(i):
            raise SystemExit(
                f"Not enough space in slot {i + 1}: the firmware needs {fmt_size(need)}, "
                f"at most {fmt_size(self.max_size(i))} is free next to slot {2 - i}. "
                f"Remove slot {2 - i} or install a smaller firmware.")
        slots = list(self.slot)
        slots[i] = need
        return Layout(slots)


def empty_layout() -> Layout:
    return Layout([0, 0])


# ---------------------------------------------------------------------------
# Partition table binary (same format as ESP-IDF gen_esp32part.py)
# ---------------------------------------------------------------------------
PT_ENTRY = struct.Struct("<2sBBII16sI")
PT_MAGIC = b"\xAA\x50"
PT_MD5_BEGIN = b"\xEB\xEB" + b"\xFF" * 14


def build_table(parts: List[Partition]) -> bytes:
    out = b"".join(PT_ENTRY.pack(PT_MAGIC, p.type, p.subtype, p.offset, p.size,
                                 p.name.encode(), p.flags) for p in parts)
    out += PT_MD5_BEGIN + hashlib.md5(out).digest()
    if len(out) > PT_MAX:
        raise ValueError("partition table too long")
    return out + b"\xFF" * (PT_MAX - len(out))


def parse_table(data: bytes) -> List[Partition]:
    parts, i = [], 0
    while i + 32 <= len(data):
        chunk = data[i:i + 32]
        if chunk[:2] == PT_MAGIC:
            _, t, st, off, size, name, flags = PT_ENTRY.unpack(chunk)
            parts.append(Partition(name.rstrip(b"\0").decode(errors="replace"), t, st, off, size, flags))
        elif chunk[:16] == PT_MD5_BEGIN:
            if hashlib.md5(data[:i]).digest() != chunk[16:]:
                raise ValueError("partition table MD5 mismatch")
            break
        else:
            break
        i += 32
    return parts


def layout_from_table(parts: List[Partition]) -> Layout:
    by_name = {p.name: p for p in parts}
    s1, s2 = by_name.get("slot1"), by_name.get("slot2")
    if not (s1 and s2 and "hub" in by_name and s1.offset == APP_START and s2.offset + s2.size == APP_END):
        raise SystemExit("This device has no ESPidi HUB layout. Run `base` first (it erases the flash).")
    return Layout([s1.size, s2.size])


# ---------------------------------------------------------------------------
# "espidi" metadata sector (C side: hub/main/slots.c)
# ---------------------------------------------------------------------------
META_MAGIC = b"ESPIDIHB"
META_FORMAT = 1
META_SLOT = struct.Struct("<16s24s16s24sII")   # 88 bytes
META_HEAD = struct.Struct("<8sHH")


@dataclass
class SlotMeta:
    id: str = ""
    name: str = ""
    version: str = ""
    author: str = ""
    image_size: int = 0
    image_crc32: int = 0


def _field(s: str, n: int) -> bytes:
    return s.encode("ascii", "replace")[:n - 1].ljust(n, b"\0")


def _str(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def pack_meta(slots: List[SlotMeta]) -> bytes:
    body = META_HEAD.pack(META_MAGIC, META_FORMAT, 0)
    for m in slots:
        body += META_SLOT.pack(_field(m.id, 16), _field(m.name, 24), _field(m.version, 16),
                               _field(m.author, 24), m.image_size, m.image_crc32)
    body += struct.pack("<I", zlib.crc32(body))
    return body + b"\xFF" * (META_SIZE - len(body))


def unpack_meta(data: bytes) -> List[SlotMeta]:
    size = META_HEAD.size + SLOTS * META_SLOT.size
    magic, fmt, _ = META_HEAD.unpack_from(data)
    crc = struct.unpack_from("<I", data, size)[0]
    if magic != META_MAGIC or fmt != META_FORMAT or zlib.crc32(data[:size]) != crc:
        return [SlotMeta() for _ in range(SLOTS)]
    out = []
    for i in range(SLOTS):
        f = META_SLOT.unpack_from(data, META_HEAD.size + i * META_SLOT.size)
        out.append(SlotMeta(_str(f[0]), _str(f[1]), _str(f[2]), _str(f[3]), f[4], f[5]))
    return out


# ---------------------------------------------------------------------------
# Firmware image
# ---------------------------------------------------------------------------
IMAGE_MAGIC = 0xE9
CHIP_ID_ESP32C3 = 0x0005
APP_DESC_MAGIC = 0xABCD5432
DESC_MAGIC = b"ESPIDI-FW-1\0"
DESC = struct.Struct("<12s16s24s16s24s")


@dataclass
class ImageInfo:
    size: int
    chip_id: int
    project: str = ""
    app_version: str = ""
    idf_version: str = ""
    desc: Optional[SlotMeta] = None
    problems: List[str] = field(default_factory=list)


def inspect_image(data: bytes) -> ImageInfo:
    info = ImageInfo(size=len(data), chip_id=-1)
    if len(data) < 0x20 + 144 or data[0] != IMAGE_MAGIC:
        info.problems.append("not an ESP32 app image (no 0xE9 header)")
        return info
    info.chip_id = struct.unpack_from("<H", data, 12)[0]
    if info.chip_id != CHIP_ID_ESP32C3:
        info.problems.append(f"built for chip id {info.chip_id}, ESPidi is an ESP32-C3 (id 5)")
    if struct.unpack_from("<I", data, 0x20)[0] == APP_DESC_MAGIC:
        info.app_version = _str(data[0x30:0x50])
        info.project = _str(data[0x50:0x70])
        info.idf_version = _str(data[0x90:0xB0])
    else:
        info.problems.append("no app description at 0x20 — is this the app .bin and not a merged image?")
    pos = data.find(DESC_MAGIC)
    if pos >= 0 and pos + DESC.size <= len(data):
        _, fid, name, ver, author = DESC.unpack_from(data, pos)
        info.desc = SlotMeta(_str(fid), _str(name), _str(ver), _str(author))
    if align_up(len(data)) > (APP_END - APP_START) - PLACEHOLDER:
        info.problems.append(f"too big: {fmt_size(len(data))}, a slot holds at most "
                             f"{fmt_size((APP_END - APP_START) - PLACEHOLDER)}")
    return info


def build_image(bootloader: bytes, hub: bytes, slots: List[Optional[bytes]], metas: List[SlotMeta]) -> bytes:
    """Fresh-install image from 0x0 to the end of the firmware area (web/espidi.js buildImage).
    Flashed at 0x0 it also clears NVS; the file partition is not part of it."""
    layout = empty_layout()
    for i, data in enumerate(slots):
        if data:
            layout = layout.with_slot(i, len(data))
    out = bytearray(b"\xFF" * APP_END)

    def put(offset: int, data: bytes) -> None:
        out[offset:offset + len(data)] = data

    put(BOOTLOADER_OFFSET, bootloader)
    put(PT_OFFSET, build_table(layout.partitions()))
    put(META_OFFSET, pack_meta(metas))
    put(HUB_OFFSET, hub)
    for i, data in enumerate(slots):
        if data:
            put(layout.offset(i), data)
    return bytes(out)


def slot_meta_for(data: bytes, info: ImageInfo, args) -> SlotMeta:
    d = info.desc or SlotMeta()
    name = args.name or d.name or os.path.splitext(os.path.basename(args.file))[0]
    return SlotMeta(id=args.id or d.id, name=name, version=args.fw_version or d.version,
                    author=args.author or d.author, image_size=len(data),
                    image_crc32=zlib.crc32(data))


# ---------------------------------------------------------------------------
# esptool
# ---------------------------------------------------------------------------
def esptool(args, *cmd: str) -> None:
    base = os.environ.get("ESPTOOL")
    if base:
        argv = base.split()
    elif shutil.which("esptool"):
        argv = ["esptool"]
    elif shutil.which("esptool.py"):
        argv = ["esptool.py"]
    else:
        argv = [sys.executable, "-m", "esptool"]
    argv += ["--chip", "esp32c3", "--port", args.port, "--baud", str(args.baud), *cmd]
    print("$ " + " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def read_flash(args, offset: int, size: int) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "read.bin")
        esptool(args, "read_flash", hex(offset), hex(size), path)
        with open(path, "rb") as f:
            return f.read()


def write_flash(args, pairs) -> None:
    with tempfile.TemporaryDirectory() as d:
        cmd = ["write_flash"]
        for i, (offset, data) in enumerate(pairs):
            path = os.path.join(d, f"part{i}.bin")
            with open(path, "wb") as f:
                f.write(data)
            cmd += [hex(offset), path]
        esptool(args, *cmd)


def read_state(args):
    parts = parse_table(read_flash(args, PT_OFFSET, PT_MAX))
    layout = layout_from_table(parts)
    meta = unpack_meta(read_flash(args, META_OFFSET, META_SIZE))
    for i in range(SLOTS):
        if not meta[i].image_size and layout.slot[i] == PLACEHOLDER:
            layout.slot[i] = 0  # placeholder entry of an empty slot
    return layout, meta


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def print_layout(layout: Layout, meta: Optional[List[SlotMeta]] = None) -> None:
    print(f"{'partition':10} {'offset':>10} {'size':>10}")
    for p in layout.partitions():
        print(f"{p.name:10} {p.offset:#10x} {fmt_size(p.size):>10}")
    for i in range(SLOTS):
        m = meta[i] if meta else None
        what = (f"{m.name} {m.version}".strip() if m and m.image_size else
                ("empty" if not layout.slot[i] else "firmware"))
        print(f"slot {i + 1} ({SLOT_BUTTON[i + 1]}): {what}, max {fmt_size(layout.max_size(i))}")
    print(f"free: {fmt_size(layout.free())}")


def cmd_check(args) -> int:
    with open(args.file, "rb") as f:
        data = f.read()
    info = inspect_image(data)
    print(f"file:     {args.file}")
    print(f"size:     {fmt_size(info.size)} (takes {fmt_size(align_up(info.size))} in a slot)")
    print(f"project:  {info.project or '-'}  version: {info.app_version or '-'}  IDF: {info.idf_version or '-'}")
    if info.desc:
        d = info.desc
        print(f"ESPidi:   id={d.id} name={d.name} version={d.version} author={d.author}")
    else:
        print("ESPidi:   no ESPIDI_FIRMWARE() descriptor — the HUB will show it as 'SLOT n'")
        if args.require_descriptor:
            info.problems.append("--require-descriptor: add ESPIDI_FIRMWARE(...) to the sketch")
    for p in info.problems:
        print(f"ERROR:    {p}")
    return 1 if info.problems else 0


def cmd_plan(args) -> int:
    layout = empty_layout()
    for i, path in enumerate((args.slot1, args.slot2)):
        if path:
            layout = layout.with_slot(i, os.path.getsize(path))
    print_layout(layout)
    return 0


def cmd_table(args) -> int:
    with open(args.out, "wb") as f:
        f.write(build_table(empty_layout().partitions()))
    print(f"wrote {args.out}")
    return 0


def cmd_image(args) -> int:
    with open(os.path.join(args.hub_dir, "bootloader.bin"), "rb") as f:
        bootloader = f.read()
    with open(os.path.join(args.hub_dir, "espidi_hub.bin"), "rb") as f:
        hub = f.read()
    slots, metas = [], []
    for path in (args.slot1, args.slot2):
        if not path:
            slots.append(None)
            metas.append(SlotMeta())
            continue
        with open(path, "rb") as f:
            data = f.read()
        info = inspect_image(data)
        if info.problems:
            for p in info.problems:
                print(f"ERROR: {path}: {p}")
            return 1
        d = info.desc or SlotMeta()
        slots.append(data)
        metas.append(SlotMeta(d.id, d.name or os.path.splitext(os.path.basename(path))[0], d.version, d.author,
                              len(data), zlib.crc32(data)))
    image = build_image(bootloader, hub, slots, metas)
    with open(args.out, "wb") as f:
        f.write(image)
    print(f"wrote {args.out} ({fmt_size(len(image))}). Flash it with:\n"
          f"  esptool --chip esp32c3 write_flash 0x0 {args.out}\n"
          "This is a fresh install: settings (NVS) are cleared.")
    return 0


def cmd_info(args) -> int:
    layout, meta = read_state(args)
    print_layout(layout, meta)
    return 0


def fetch(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as r:
        return r.read()


def cmd_base(args) -> int:
    if args.hub_dir:
        def load(name):
            with open(os.path.join(args.hub_dir, name), "rb") as f:
                return f.read()
    else:
        tag = "latest/download" if args.release == "latest" else f"download/{args.release}"
        def load(name):
            return fetch(f"{RELEASES}/{tag}/{name}")
    bootloader, hub = load("bootloader.bin"), load("espidi_hub.bin")
    if not args.yes:
        answer = input("This erases the whole flash, including all settings. Continue? [y/N] ")
        if answer.strip().lower() != "y":
            return 1
    esptool(args, "erase_flash")
    write_flash(args, [
        (BOOTLOADER_OFFSET, bootloader),
        (PT_OFFSET, build_table(empty_layout().partitions())),
        (META_OFFSET, pack_meta([SlotMeta() for _ in range(SLOTS)])),
        (HUB_OFFSET, hub),
    ])
    print("ESPidi HUB installed. Next: install --slot 1 <firmware.bin>")
    return 0


def cmd_install(args) -> int:
    i = args.slot - 1
    with open(args.file, "rb") as f:
        data = f.read()
    info = inspect_image(data)
    if info.problems:
        for p in info.problems:
            print(f"ERROR: {p}")
        return 1
    layout, meta = read_state(args)
    new = layout.with_slot(i, len(data))
    meta[i] = slot_meta_for(data, info, args)
    print(f"slot {args.slot}: {meta[i].name} {meta[i].version} -> {new.offset(i):#x}, {fmt_size(new.alloc(i))}")
    # Image first: until the partition table changes, the old table still describes the
    # device, and a half-written slot simply fails image validation (the HUB starts).
    write_flash(args, [(new.offset(i), data)])
    write_flash(args, [(PT_OFFSET, build_table(new.partitions())), (META_OFFSET, pack_meta(meta))])
    print_layout(new, meta)
    return 0


def cmd_remove(args) -> int:
    i = args.slot - 1
    layout, meta = read_state(args)
    new = layout.with_slot(i, 0)
    meta[i] = SlotMeta()
    # Erase first: the placeholder entry must not start with a leftover image header.
    esptool(args, "erase_region", hex(new.offset(i)), hex(PLACEHOLDER))
    write_flash(args, [(PT_OFFSET, build_table(new.partitions())), (META_OFFSET, pack_meta(meta))])
    print_layout(new, meta)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ESPidi HUB installer", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Commands" + __doc__.split("Commands", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def device(p):
        p.add_argument("-p", "--port", required=True, help="serial port, e.g. /dev/cu.usbmodem1101")
        p.add_argument("-b", "--baud", type=int, default=921600)

    p = sub.add_parser("check", help="inspect a firmware .bin")
    p.add_argument("file")
    p.add_argument("--require-descriptor", action="store_true", help="fail without ESPIDI_FIRMWARE()")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("plan", help="show the layout for one or two firmwares")
    p.add_argument("--slot1")
    p.add_argument("--slot2")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("table", help="write the empty partition table")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("image", help="build one fresh-install image: HUB + up to two firmwares")
    p.add_argument("--hub-dir", required=True, help="folder with bootloader.bin and espidi_hub.bin")
    p.add_argument("--slot1")
    p.add_argument("--slot2")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("info", help="show what is installed")
    device(p)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("base", help="first install: erase flash, write bootloader and HUB")
    device(p)
    p.add_argument("--hub-dir", help="folder with bootloader.bin and espidi_hub.bin (default: GitHub release)")
    p.add_argument("--release", default="latest", help="release tag to download (default: latest)")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask before erasing")
    p.set_defaults(func=cmd_base)

    p = sub.add_parser("install", help="put a firmware into a slot")
    device(p)
    p.add_argument("--slot", type=int, choices=(1, 2), required=True)
    p.add_argument("file")
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--fw-version")
    p.add_argument("--author")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("remove", help="empty a slot")
    device(p)
    p.add_argument("--slot", type=int, choices=(1, 2), required=True)
    p.set_defaults(func=cmd_remove)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
