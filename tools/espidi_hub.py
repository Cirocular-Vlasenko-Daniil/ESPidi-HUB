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
    cover FILE > cover.h    turn an image into a 64x32 cover for a sketch (ESPIDI_COVER)
    repo [DIR | owner/repo] check an ESPidi-ready repository (espidi.json); --sync-cover,
                            --build BIN [--publish] for the firmware workflow
    info -p PORT            what is installed on a device
    base -p PORT            first install: erase flash, write bootloader + HUB
    install -p PORT --slot N FILE|owner/repo [--cover IMG]
    remove -p PORT --slot N

Needs esptool (`pip install esptool`) for the device commands.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import json
import urllib.error
import urllib.parse
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
SLOT_BUTTON = {1: "PLAY", 2: "PAGE"}

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


def pack_meta(slots: List[SlotMeta], covers: Optional[List[Optional[bytes]]] = None) -> bytes:
    body = META_HEAD.pack(META_MAGIC, META_FORMAT, 0)
    for m in slots:
        body += META_SLOT.pack(_field(m.id, 16), _field(m.name, 24), _field(m.version, 16),
                               _field(m.author, 24), m.image_size, m.image_crc32)
    body += struct.pack("<I", zlib.crc32(body))
    out = bytearray(body + b"\xFF" * (META_SIZE - len(body)))
    for i, cover in enumerate(covers or []):
        if cover:
            out[COVER_OFFSETS[i]:COVER_OFFSETS[i] + COVER_BLOCK] = pack_cover(cover)
    return bytes(out)


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
# Covers: 64x32, 1 bit per pixel, rows top to bottom, 8 bytes per row, MSB = leftmost
# pixel — the Adafruit GFX drawBitmap() / image2cpp "horizontal" format. 1 = lit
# (white on the OLED). Stored in the "espidi" sector (one block per slot) and, optionally,
# in a firmware (ESPIDI_COVER() from the SDK).
# ---------------------------------------------------------------------------
COVER_W, COVER_H = 64, 32
COVER_BYTES = COVER_W * COVER_H // 8                # 256
COVER_MAGIC = b"ESPIDICV"
COVER_HEAD = struct.Struct("<8sHHI")                # magic, width, height, reserved
COVER_BLOCK = COVER_HEAD.size + COVER_BYTES + 4     # + CRC-32 = 276 bytes
COVER_OFFSETS = (0x100, 0x300)                      # inside the "espidi" sector
FW_COVER_MAGIC = b"ESPIDI-CV-1\0"


def pack_cover(bits: bytes) -> bytes:
    assert len(bits) == COVER_BYTES
    body = COVER_HEAD.pack(COVER_MAGIC, COVER_W, COVER_H, 0) + bits
    return body + struct.pack("<I", zlib.crc32(body))


def unpack_cover(data: bytes, offset: int) -> Optional[bytes]:
    block = data[offset:offset + COVER_BLOCK]
    if len(block) < COVER_BLOCK:
        return None
    magic, w, h, _ = COVER_HEAD.unpack_from(block)
    body = block[:-4]
    if magic != COVER_MAGIC or (w, h) != (COVER_W, COVER_H) or zlib.crc32(body) != struct.unpack_from("<I", block, len(body))[0]:
        return None
    return bytes(body[COVER_HEAD.size:])


def unpack_covers(data: bytes) -> List[Optional[bytes]]:
    return [unpack_cover(data, o) for o in COVER_OFFSETS]


def _read_png(data: bytes):
    """Minimal PNG reader (non-interlaced, any colour type, 1–16 bits) -> w, h, [(lum, alpha)] rows."""
    pos, idat, plte, trns, ihdr = 8, b"", b"", b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"PLTE":
            plte = chunk
        elif typ == b"tRNS":
            trns = chunk
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    w, h, depth, ctype, _, _, interlace = ihdr
    if interlace:
        raise ValueError("interlaced PNG is not supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = (w * depth * channels + 7) // 8
    bpp = max(1, depth * channels // 8)
    raw, rows, prev, i = zlib.decompress(idat), [], bytearray(stride), 0
    for _ in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b, c = prev[x], (prev[x - bpp] if x >= bpp else 0)
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line

    def samples(row: bytes) -> List[int]:
        if depth == 8:
            return list(row)
        if depth == 16:
            return list(row[0::2])              # high bytes
        per, mask = 8 // depth, (1 << depth) - 1
        return [(byte >> (8 - depth * (k + 1))) & mask for byte in row for k in range(per)]

    lum = lambda r, g, b: (r * 299 + g * 587 + b * 114) // 1000  # noqa: E731
    gray = (lambda v: v * 255 // ((1 << depth) - 1)) if depth < 8 else (lambda v: v)  # noqa: E731
    pixels = []
    for row in rows:
        s, line = samples(row), []
        for x in range(w):
            if ctype == 0:
                line.append((gray(s[x]), 255))
            elif ctype == 2:
                line.append((lum(*s[3 * x:3 * x + 3]), 255))
            elif ctype == 3:
                idx = s[x]
                line.append((lum(*plte[3 * idx:3 * idx + 3]), trns[idx] if idx < len(trns) else 255))
            elif ctype == 4:
                line.append((s[2 * x], s[2 * x + 1]))
            else:
                line.append((lum(*s[4 * x:4 * x + 3]), s[4 * x + 3]))
        pixels.append(line)
    return w, h, pixels


def _read_pbm(data: bytes):
    """PBM P1/P4 -> w, h, [(lum, alpha)] rows. In PBM 1 is black."""
    m = re.match(rb"(P[14])(?:\s+|#[^\n]*\n)+(\d+)(?:\s+|#[^\n]*\n)+(\d+)\s", data)
    if not m:
        raise ValueError("bad PBM header")
    kind, w, h, body = m.group(1), int(m.group(2)), int(m.group(3)), data[m.end():]
    if kind == b"P4":
        stride = (w + 7) // 8
        bit = lambda x, y: (body[y * stride + x // 8] >> (7 - x % 8)) & 1  # noqa: E731
    else:
        digits = [c - 48 for c in body if c in b"01"]
        bit = lambda x, y: digits[y * w + x]  # noqa: E731
    return w, h, [[(0 if bit(x, y) else 255, 255) for x in range(w)] for y in range(h)]


def pixels_to_cover(w: int, h: int, pixels, invert: bool = False) -> bytes:
    """64x32 or an exact multiple (128x64, …): each block of k*k pixels becomes one pixel,
    lit if at least half of it is light (and opaque)."""
    if w % COVER_W or h % COVER_H or w // COVER_W != h // COVER_H:
        raise ValueError(f"{w}x{h}: a cover is 64x32 or a multiple of it (128x64, 192x96, ...)")
    k = w // COVER_W
    out = bytearray(COVER_BYTES)
    for y in range(COVER_H):
        for x in range(COVER_W):
            lit = sum(1 for dy in range(k) for dx in range(k)
                      if pixels[y * k + dy][x * k + dx][1] >= 128 and pixels[y * k + dy][x * k + dx][0] >= 128)
            if (lit * 2 >= k * k) != invert:
                out[y * 8 + x // 8] |= 0x80 >> (x % 8)
    return bytes(out)


def load_cover(path: str, invert: bool = False) -> bytes:
    with open(path, "rb") as f:
        return cover_from_bytes(f.read(), path, invert)


def cover_from_bytes(data: bytes, name: str = "", invert: bool = False) -> bytes:
    """PNG, PBM, or (when `name` ends with .bin/.raw) the 256 cover bytes themselves."""
    if name.lower().endswith((".bin", ".raw")):
        if len(data) != COVER_BYTES:
            raise ValueError(f"a raw cover is exactly {COVER_BYTES} bytes")
        return bytes(b ^ 0xFF for b in data) if invert else data
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h, px = _read_png(data)
    elif data[:2] in (b"P1", b"P4"):
        w, h, px = _read_pbm(data)
    else:
        raise ValueError("a cover is a PNG, a PBM or a raw 256-byte .bin")
    return pixels_to_cover(w, h, px, invert)


def cover_png(bits: bytes) -> bytes:
    """A 64x32 1-bit grayscale PNG of a cover: each PNG row is the cover row, byte for byte."""
    raw = b"".join(b"\0" + bits[y * 8:(y + 1) * 8] for y in range(COVER_H))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", COVER_W, COVER_H, 1, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def cover_ascii(bits: bytes) -> str:
    return "\n".join("".join("#" if bits[y * 8 + x // 8] & (0x80 >> (x % 8)) else "." for x in range(COVER_W))
                     for y in range(COVER_H))


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
    cover: Optional[bytes] = None
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
    pos = data.find(FW_COVER_MAGIC)
    if pos >= 0 and pos + len(FW_COVER_MAGIC) + COVER_BYTES <= len(data):
        info.cover = data[pos + len(FW_COVER_MAGIC):pos + len(FW_COVER_MAGIC) + COVER_BYTES]
    if align_up(len(data)) > (APP_END - APP_START) - PLACEHOLDER:
        info.problems.append(f"too big: {fmt_size(len(data))}, a slot holds at most "
                             f"{fmt_size((APP_END - APP_START) - PLACEHOLDER)}")
    return info


def build_image(bootloader: bytes, hub: bytes, slots: List[Optional[bytes]], metas: List[SlotMeta],
                covers: Optional[List[Optional[bytes]]] = None) -> bytes:
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
    put(META_OFFSET, pack_meta(metas, covers))
    put(HUB_OFFSET, hub)
    for i, data in enumerate(slots):
        if data:
            put(layout.offset(i), data)
    return bytes(out)


def slot_meta_for(data: bytes, info: ImageInfo, args, fallback: Optional[SlotMeta] = None) -> SlotMeta:
    d = info.desc or fallback or SlotMeta()
    name = args.name or d.name or os.path.splitext(os.path.basename(args.file))[0]
    return SlotMeta(id=args.id or d.id, name=name, version=args.fw_version or d.version,
                    author=args.author or d.author, image_size=len(data),
                    image_crc32=zlib.crc32(data))


# ---------------------------------------------------------------------------
# ESPidi-ready repositories. espidi.json (next to the sketch folder) says where the built
# firmware and its cover are, so the composer site (web/github.js) and this tool take them
# straight from the repository — GitHub release downloads cannot be read by a web page.
# Format: docs/FIRMWARE_GUIDE.md, "Publishing".
# ---------------------------------------------------------------------------
MANIFEST = "espidi.json"
MANIFEST_FORMAT = 1
MANIFEST_TEXT = {"id": 15, "name": 23, "version": 15, "author": 23}   # fallbacks without a descriptor
MANIFEST_PROBLEMS = {
    "json": "espidi.json is not valid JSON",
    "object": "espidi.json must be a JSON object",
    "format": '"espidi" must be 1',
    "firmware": '"firmware" must be the path of the .bin inside the repository, e.g. "espidi/MySketch.ino.bin"',
    "cover": '"cover" must be the path of a .png inside the repository, e.g. "cover.png"',
    "summary": '"summary" must be text or {"en": "...", "ru": "..."}',
    "sketch": '"sketch" must be the name of the sketch folder',
    "text": '"id"/"version" (max 15) and "name"/"author" (max 23) must be short text',
}
RAW_URL = "https://raw.githubusercontent.com"
JSDELIVR_URL = "https://cdn.jsdelivr.net/gh"
API_URL = "https://api.github.com"


class RepoError(Exception):
    pass


def _repo_path(p, ext: str) -> bool:
    return (isinstance(p, str) and p.lower().endswith(ext) and not p.startswith("/") and "\\" not in p
            and all(seg not in ("", ".", "..") for seg in p.split("/")))


def check_manifest(m) -> List[str]:
    """Problem codes (keys of MANIFEST_PROBLEMS); validateManifest() in web/espidi.js is the same."""
    if not isinstance(m, dict):
        return ["object"]
    out = []
    if isinstance(m.get("espidi"), bool) or m.get("espidi") != MANIFEST_FORMAT:
        out.append("format")
    if not _repo_path(m.get("firmware"), ".bin"):
        out.append("firmware")
    if "cover" in m and not _repo_path(m["cover"], ".png"):
        out.append("cover")
    summary = m.get("summary", "")
    if not (isinstance(summary, str) or (isinstance(summary, dict) and all(isinstance(v, str) for v in summary.values()))):
        out.append("summary")
    if "sketch" in m and not (isinstance(m["sketch"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", m["sketch"])):
        out.append("sketch")
    if any(k in m and not (isinstance(m[k], str) and len(m[k]) <= n) for k, n in MANIFEST_TEXT.items()):
        out.append("text")
    return out


@dataclass
class RepoSpec:
    owner: str
    repo: str
    ref: str = ""      # branch, tag or commit; "" = the default branch
    path: str = ""     # folder of espidi.json inside the repository

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}" + (f"/{self.path}" if self.path else "") + (f"@{self.ref}" if self.ref else "")


_GITHUB_LINK = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+)(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?$", re.I)
_RAW_LINK = re.compile(r"^(?:https?://)?raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)$", re.I)


def parse_spec(text: str) -> RepoSpec:
    """owner/repo[/folder][@ref], a github.com link (…/tree/<ref>/<folder>, …/blob/<ref>/…/espidi.json)
    or a raw.githubusercontent.com link. parseSpec() in web/github.js is the same."""
    s = re.sub(r"[?#].*$", "", text.strip()).rstrip("/")
    m = _GITHUB_LINK.match(s) or _RAW_LINK.match(s)
    if m:
        owner, repo, ref, path = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    else:
        body, _, ref = s.partition("@")
        parts = body.split("/")
        if len(parts) < 2:
            raise RepoError(f"{text!r}: write owner/repo, owner/repo@branch or a GitHub link")
        owner, repo, path = parts[0], parts[1], "/".join(parts[2:])
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    path = path.strip("/")
    if path == MANIFEST or path.endswith("/" + MANIFEST):
        path = path[:-len(MANIFEST)].rstrip("/")
    ok = (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo)
          and (not path or all(seg not in ("", ".", "..") for seg in path.split("/")))
          and re.fullmatch(r"[^\s~^:?*\[\\]*", ref) and ".." not in ref and not ref.startswith("/") and not ref.endswith("/"))
    if not ok:
        raise RepoError(f"{text!r}: write owner/repo, owner/repo@branch or a GitHub link")
    return RepoSpec(owner, repo, ref, path)


def _http_get(url: str, accept: str = "") -> Optional[bytes]:
    """The body, or None for 404."""
    req = urllib.request.Request(url, headers={"User-Agent": "espidi-hub", **({"Accept": accept} if accept else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _quote_path(p: str) -> str:
    return "/".join(urllib.parse.quote(seg, safe="") for seg in p.split("/"))


def resolve_commit(spec: RepoSpec) -> Optional[str]:
    """The commit the ref points to, so all files come from one commit; None when the API is
    unreachable or rate-limited (60 requests an hour without a token) — files then come by ref."""
    try:
        body = _http_get(f"{API_URL}/repos/{spec.owner}/{spec.repo}/commits/{_quote_path(spec.ref or 'HEAD')}",
                         "application/vnd.github.sha")
    except urllib.error.HTTPError as e:
        if e.code == 422:
            raise RepoError(f"no branch, tag or commit {spec.ref!r} in {spec.owner}/{spec.repo}")
        return None
    except (urllib.error.URLError, OSError):
        return None
    if body is None:
        raise RepoError(f"no repository {spec.owner}/{spec.repo} on GitHub (or it is private)")
    sha = body.decode("ascii", "replace").strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


class LocalRepo:
    def __init__(self, path: str):
        self.dir = path
        self.where = os.path.abspath(path)

    def read(self, rel: str) -> Optional[bytes]:
        try:
            with open(os.path.join(self.dir, *rel.split("/")), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None


class GithubRepo:
    def __init__(self, spec: RepoSpec):
        self.spec = spec
        self.sha = resolve_commit(spec)
        self.at = self.sha or spec.ref or "HEAD"
        self.where = f"{spec.owner}/{spec.repo}" + (f"/{spec.path}" if spec.path else "") + f" at {self.at[:12]}"

    def read(self, rel: str) -> Optional[bytes]:
        s = self.spec
        path = _quote_path("/".join(x for x in (s.path, rel) if x))
        at = _quote_path(self.at)
        urls = [f"{RAW_URL}/{s.owner}/{s.repo}/{at}/{path}",
                f"{JSDELIVR_URL}/{s.owner}/{s.repo}" + ("" if self.at == "HEAD" else f"@{at}") + f"/{path}"]
        for url in urls:
            try:
                return _http_get(url)
            except (urllib.error.URLError, OSError):
                continue
        raise RepoError(f"could not reach GitHub for {rel}")


def open_repo(target: str):
    return LocalRepo(target) if os.path.isdir(target) else GithubRepo(parse_spec(target))


@dataclass
class RepoReport:
    manifest: Optional[dict] = None
    firmware: Optional[bytes] = None
    info: Optional[ImageInfo] = None
    cover: Optional[bytes] = None
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def meta(self) -> SlotMeta:
        """What the HUB shows: the descriptor, else the fallbacks in espidi.json."""
        if self.info and self.info.desc:
            return self.info.desc
        m = self.manifest or {}
        return SlotMeta(m.get("id", ""), m.get("name", ""), m.get("version", ""), m.get("author", ""))


def check_repo(src, need_firmware: bool = True) -> RepoReport:
    rep = RepoReport()
    raw = src.read(MANIFEST)
    if raw is None:
        rep.problems.append(f"no {MANIFEST} in {src.where} — not an ESPidi-ready repository")
        return rep
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        rep.problems.append(MANIFEST_PROBLEMS["json"])
        return rep
    codes = check_manifest(m)
    rep.problems += [MANIFEST_PROBLEMS[c] for c in codes]
    if codes:
        return rep
    rep.manifest = m
    if "cover" in m:
        data = src.read(m["cover"])
        if data is None:
            rep.problems.append(f"cover {m['cover']} is not in the repository")
        else:
            try:
                rep.cover = cover_from_bytes(data, m["cover"])
            except ValueError as e:
                rep.problems.append(f"cover {m['cover']}: {e}")
    rep.firmware = src.read(m["firmware"])
    if rep.firmware is None:
        if need_firmware:
            rep.problems.append(f"firmware {m['firmware']} is not in the repository — publish a build "
                                "(the firmware workflow does it when the version changes)")
        return rep
    rep.info = inspect_image(rep.firmware)
    rep.problems += [f"{m['firmware']}: {p}" for p in rep.info.problems]
    d = rep.info.desc
    if not d and not m.get("name"):
        rep.problems.append(f"{m['firmware']} has no ESPIDI_FIRMWARE() and {MANIFEST} has no \"name\"")
    if d:
        for k in ("id", "name", "version", "author"):
            if m.get(k) and m[k] != getattr(d, k):
                rep.warnings.append(f'{MANIFEST} "{k}" is {m[k]!r}, the firmware says {getattr(d, k)!r} — the firmware wins')
    if rep.cover and rep.info.cover and rep.cover != rep.info.cover:
        rep.warnings.append(f"the cover inside {m['firmware']} differs from {m['cover']} — run repo --sync-cover and rebuild")
    return rep


def sync_cover(repo_dir: str, m: dict, cover: Optional[bytes]) -> Optional[str]:
    """Writes <sketch>/cover.h from the manifest's cover. Returns the path, or None if there is nothing to write."""
    if not cover or not m.get("sketch"):
        return None
    path = os.path.join(repo_dir, m["sketch"], "cover.h")
    text = cover_header(cover, m["cover"], generated=True)
    old = None
    if os.path.exists(path):
        with open(path) as f:
            old = f.read()
    if old != text:
        with open(path, "w") as f:
            f.write(text)
    return path


def publish_build(repo_dir: str, rep: RepoReport, build: bytes) -> bool:
    """Copies a fresh build to the manifest's firmware path when its version (or cover) is new.
    Same version, same cover: nothing to publish (builds differ only by their timestamp)."""
    new = inspect_image(build)
    old = rep.info
    if old and old.desc and new.desc and old.desc.version == new.desc.version and old.cover == new.cover:
        return False
    path = os.path.join(repo_dir, *rep.manifest["firmware"].split("/"))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(build)
    return True


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
    sector = read_flash(args, META_OFFSET, META_SIZE)
    meta, covers = unpack_meta(sector), unpack_covers(sector)
    for i in range(SLOTS):
        if not meta[i].image_size and layout.slot[i] == PLACEHOLDER:
            layout.slot[i] = 0  # placeholder entry of an empty slot
    return layout, meta, covers


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def print_layout(layout: Layout, meta: Optional[List[SlotMeta]] = None, covers=None) -> None:
    print(f"{'partition':10} {'offset':>10} {'size':>10}")
    for p in layout.partitions():
        print(f"{p.name:10} {p.offset:#10x} {fmt_size(p.size):>10}")
    for i in range(SLOTS):
        m = meta[i] if meta else None
        what = (f"{m.name} {m.version}".strip() if m and m.image_size else
                ("empty" if not layout.slot[i] else "firmware"))
        cover = ", cover" if covers and covers[i] else ""
        print(f"slot {i + 1} ({SLOT_BUTTON[i + 1]}): {what}{cover}, max {fmt_size(layout.max_size(i))}")
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
    print(f"cover:    {'ESPIDI_COVER() inside' if info.cover else 'none (the HUB shows the name only)'}")
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
    covers = []
    for i, cover_path in enumerate((args.cover1, args.cover2)):
        if cover_path:
            covers.append(load_cover(cover_path))
        else:
            covers.append(inspect_image(slots[i]).cover if slots[i] else None)
    image = build_image(bootloader, hub, slots, metas, covers)
    with open(args.out, "wb") as f:
        f.write(image)
    print(f"wrote {args.out} ({fmt_size(len(image))}). Flash it with:\n"
          f"  esptool --chip esp32c3 write_flash 0x0 {args.out}\n"
          "This is a fresh install: settings (NVS) are cleared.")
    return 0


def cmd_info(args) -> int:
    layout, meta, covers = read_state(args)
    print_layout(layout, meta, covers)
    return 0


def cover_header(bits: bytes, source: str = "", generated: bool = False) -> str:
    """A cover.h for a sketch: the picture as a comment, then ESPIDI_COVER(...)."""
    lines = [", ".join(f"0x{b:02x}" for b in bits[i:i + 16]) for i in range(0, COVER_BYTES, 16)]
    head = ""
    if generated:
        head = (f"// Generated from {source} — do not edit. Change {source}, then run\n"
                "//   python3 <ESPidi-HUB>/tools/espidi_hub.py repo . --sync-cover\n"
                "// (the firmware workflow does it before every build).\n")
    head += f"// ESPidi HUB cover{' from ' + source if source else ''} (64x32, 1 = lit):\n"
    return (head + "\n".join("// " + row for row in cover_ascii(bits).splitlines()) +
            "\n#pragma once\n#include <ESPidiHub.h>\n\nESPIDI_COVER(\n  " + ",\n  ".join(lines) + ");\n")


def cmd_cover(args) -> int:
    print(cover_header(load_cover(args.file, args.invert), os.path.basename(args.file)), end="")
    return 0


def cmd_repo(args) -> int:
    try:
        src = open_repo(args.target)
        if isinstance(src, GithubRepo) and (args.sync_cover or args.publish):
            raise RepoError("--sync-cover and --publish work on a local checkout")
        rep = check_repo(src, need_firmware=not args.build)
    except RepoError as e:
        print(f"ERROR:    {e}")
        return 1
    print(f"repo:     {src.where}")
    m = rep.manifest
    if m:
        print(f"sketch:   {m.get('sketch', '-')}")
        print(f"cover:    {m.get('cover', 'none (the firmware cover or the name)')}")
        if rep.info:
            d = rep.meta()
            print(f"firmware: {m['firmware']} — {d.name} {d.version}, {fmt_size(len(rep.firmware))}")
        else:
            print(f"firmware: {m['firmware']} — not published yet")
        if args.sync_cover:
            path = sync_cover(args.target, m, rep.cover)
            print(f"cover.h:  {path or 'nothing to write (needs sketch and cover in ' + MANIFEST + ')'}")
    if args.build and m:
        with open(args.build, "rb") as f:
            build = f.read()
        info = inspect_image(build)
        if not info.desc:
            info.problems.append("no ESPIDI_FIRMWARE() — add it to the sketch")
        rep.problems += [f"{args.build}: {p}" for p in info.problems]
        if rep.cover and info.cover != rep.cover:
            rep.warnings.append(f"the cover inside {args.build} differs from {m['cover']} — run repo --sync-cover before building")
        if args.publish and not rep.problems:
            if publish_build(args.target, rep, build):
                print(f"publish:  {info.desc.name} {info.desc.version} -> {m['firmware']}")
            else:
                print(f"publish:  {info.desc.version} is already published (change the version to publish a new build)")
    for w in rep.warnings:
        print(f"WARNING:  {w}")
    for p in rep.problems:
        print(f"ERROR:    {p}")
    return 1 if rep.problems else 0


def load_firmware_arg(target: str):
    """A .bin path, or an ESPidi-ready repository (local folder or owner/repo):
    -> (data, info, cover from the repository or None, name fallbacks)."""
    if os.path.isfile(target):
        with open(target, "rb") as f:
            data = f.read()
        return data, inspect_image(data), None, None
    src = open_repo(target)
    rep = check_repo(src)
    if rep.problems:
        raise RepoError("; ".join(rep.problems))
    print(f"from {src.where}: {rep.manifest['firmware']}")
    return rep.firmware, rep.info, rep.cover, rep.meta()


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
    try:
        data, info, repo_cover, fallback = load_firmware_arg(args.file)
    except RepoError as e:
        print(f"ERROR: {e}")
        return 1
    if info.problems:
        for p in info.problems:
            print(f"ERROR: {p}")
        return 1
    own = repo_cover or info.cover
    cover = None if args.no_cover else (load_cover(args.cover, args.invert_cover) if args.cover else own)
    layout, meta, covers = read_state(args)
    new = layout.with_slot(i, len(data))
    meta[i] = slot_meta_for(data, info, args, fallback)
    covers[i] = cover
    print(f"slot {args.slot}: {meta[i].name} {meta[i].version} -> {new.offset(i):#x}, {fmt_size(new.alloc(i))}")
    # Image first: until the partition table changes, the old table still describes the
    # device, and a half-written slot simply fails image validation (the HUB starts).
    write_flash(args, [(new.offset(i), data)])
    write_flash(args, [(PT_OFFSET, build_table(new.partitions())), (META_OFFSET, pack_meta(meta, covers))])
    print_layout(new, meta, covers)
    return 0


def cmd_remove(args) -> int:
    i = args.slot - 1
    layout, meta, covers = read_state(args)
    new = layout.with_slot(i, 0)
    meta[i] = SlotMeta()
    covers[i] = None
    # Erase first: the placeholder entry must not start with a leftover image header.
    esptool(args, "erase_region", hex(new.offset(i)), hex(PLACEHOLDER))
    write_flash(args, [(PT_OFFSET, build_table(new.partitions())), (META_OFFSET, pack_meta(meta, covers))])
    print_layout(new, meta, covers)
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
    p.add_argument("--cover1", help="cover for slot 1 (default: the one inside the firmware)")
    p.add_argument("--cover2")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("cover", help="turn an image into a cover.h for a sketch (ESPIDI_COVER)")
    p.add_argument("file", help="PNG or PBM, 64x32 or a multiple of it")
    p.add_argument("--invert", action="store_true", help="for black-on-white art")
    p.set_defaults(func=cmd_cover)

    p = sub.add_parser("repo", help="check an ESPidi-ready repository (espidi.json)")
    p.add_argument("target", nargs="?", default=".", help="local folder (default .) or owner/repo[/folder][@ref] / GitHub link")
    p.add_argument("--sync-cover", action="store_true", help="write <sketch>/cover.h from the cover in espidi.json")
    p.add_argument("--build", metavar="BIN", help="check a fresh build too")
    p.add_argument("--publish", action="store_true", help="with --build: copy it to the firmware path if its version is new")
    p.set_defaults(func=cmd_repo)

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
    p.add_argument("file", help="firmware .bin, or an ESPidi-ready repository: folder or owner/repo[@ref]")
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--fw-version")
    p.add_argument("--author")
    p.add_argument("--cover", help="PNG or PBM, 64x32 or a multiple (default: the repository's, else the one inside the firmware)")
    p.add_argument("--invert-cover", action="store_true", help="for black-on-white art")
    p.add_argument("--no-cover", action="store_true")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("remove", help="empty a slot")
    device(p)
    p.add_argument("--slot", type=int, choices=(1, 2), required=True)
    p.set_defaults(func=cmd_remove)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
