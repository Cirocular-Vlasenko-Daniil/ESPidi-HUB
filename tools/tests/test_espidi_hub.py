import glob
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import espidi_hub as hub  # noqa: E402


def find_gen_esp32part():
    """gen_esp32part.py from ESP-IDF or from an installed Arduino ESP32 core, if any."""
    candidates = []
    if os.environ.get("IDF_PATH"):
        candidates.append(os.path.join(os.environ["IDF_PATH"], "components/partition_table/gen_esp32part.py"))
    candidates += glob.glob(os.path.expanduser("~/Library/Arduino15/packages/esp32/hardware/esp32/*/tools/gen_esp32part.py"))
    candidates += glob.glob(os.path.expanduser("~/.arduino15/packages/esp32/hardware/esp32/*/tools/gen_esp32part.py"))
    return next((c for c in candidates if os.path.exists(c)), None)


def fake_image(size, chip_id=5, desc=None):
    """Minimal app image: header, extended header with chip id, app description, padding."""
    head = bytes([0xE9, 1, 2, 0x20]) + b"\0" * 4
    ext = bytes([0xEE, 0, 0, 0]) + struct.pack("<H", chip_id) + b"\0" * 10
    seg = b"\0" * 8
    app_desc = struct.pack("<I", hub.APP_DESC_MAGIC) + b"\0" * 12 + b"1.0".ljust(32, b"\0") + \
        b"demo".ljust(32, b"\0") + b"\0" * 32 + b"v5.5".ljust(32, b"\0")
    data = head + ext + seg + app_desc
    if desc:
        data += b"\0" * (-len(data) % 4) + hub.DESC.pack(hub.DESC_MAGIC, *(hub._field(v, n) for v, n in zip(desc, (16, 24, 16, 24))))
    return data + b"\xAA" * (size - len(data))


class LayoutTest(unittest.TestCase):
    AREA = hub.APP_END - hub.APP_START

    def test_empty_layout_matches_csv(self):
        parts = hub.empty_layout().partitions()
        self.assertEqual([(p.name, p.offset, p.size) for p in parts], [
            ("nvs", 0x9000, 0x4000), ("espidi", 0xD000, 0x1000), ("otadata", 0xE000, 0x2000),
            ("hub", 0x10000, 0x40000), ("slot1", 0x50000, 0x10000), ("slot2", 0x2F0000, 0x10000),
            ("spiffs", 0x300000, 0x100000)])

    def test_slots_grow_towards_each_other(self):
        lay = hub.empty_layout().with_slot(0, 430_000).with_slot(1, 1_300_000)
        self.assertEqual(lay.offset(0), hub.APP_START)
        self.assertEqual(lay.offset(1) + lay.alloc(1), hub.APP_END)
        self.assertEqual(lay.alloc(0), hub.align_up(430_000))
        self.assertLessEqual(lay.offset(0) + lay.alloc(0), lay.offset(1))
        self.assertEqual(lay.free(), self.AREA - lay.alloc(0) - lay.alloc(1))

    def test_installing_one_slot_never_moves_the_other(self):
        lay = hub.empty_layout().with_slot(1, 900_000)
        before = lay.offset(1)
        lay = lay.with_slot(0, 1_500_000).with_slot(0, 200_000)
        self.assertEqual(lay.offset(1), before)

    def test_big_firmware_when_other_slot_empty(self):
        size = self.AREA - hub.PLACEHOLDER
        lay = hub.empty_layout().with_slot(0, size)
        self.assertEqual(lay.alloc(0), size)
        with self.assertRaises(SystemExit):
            hub.empty_layout().with_slot(0, size + 1)

    def test_no_room_next_to_a_big_firmware(self):
        lay = hub.empty_layout().with_slot(0, 2_000_000)
        with self.assertRaises(SystemExit):
            lay.with_slot(1, 1_000_000)
        lay.with_slot(1, lay.max_size(1))  # exactly the gap fits

    def test_layout_round_trips_through_table(self):
        lay = hub.empty_layout().with_slot(0, 430_000).with_slot(1, 600_000)
        parts = hub.parse_table(hub.build_table(lay.partitions()))
        self.assertEqual(hub.layout_from_table(parts).slot, lay.slot)

    def test_foreign_table_is_rejected(self):
        arduino_default = [hub.Partition("nvs", 1, 2, 0x9000, 0x5000), hub.Partition("app0", 0, 0x10, 0x10000, 0x140000)]
        with self.assertRaises(SystemExit):
            hub.layout_from_table(arduino_default)

    def test_table_md5_is_checked(self):
        data = bytearray(hub.build_table(hub.empty_layout().partitions()))
        data[10] ^= 1
        with self.assertRaises(ValueError):
            hub.parse_table(bytes(data))


class GenEsp32PartTest(unittest.TestCase):
    """Our table must be byte-identical to ESP-IDF's own generator."""

    def setUp(self):
        self.gen = find_gen_esp32part()
        if not self.gen:
            self.skipTest("gen_esp32part.py not found")

    def gen_from_csv(self, csv_text):
        with tempfile.TemporaryDirectory() as d:
            csv, out = os.path.join(d, "p.csv"), os.path.join(d, "p.bin")
            with open(csv, "w") as f:
                f.write(csv_text)
            subprocess.run([sys.executable, self.gen, "-q", "--flash-size", "4MB", csv, out], check=True)
            with open(out, "rb") as f:
                return f.read()

    def test_repo_csv(self):
        with open(os.path.join(ROOT, "hub", "partitions.csv")) as f:
            self.assertEqual(self.gen_from_csv(f.read()), hub.build_table(hub.empty_layout().partitions()))

    def test_filled_layout(self):
        lay = hub.empty_layout().with_slot(0, 430_000).with_slot(1, 1_700_000)
        names = {0x00: "app", 0x01: "data"}
        rows = [f"{p.name},{names.get(p.type, hex(p.type))},{p.subtype:#x},{p.offset:#x},{p.size:#x},"
                for p in lay.partitions()]
        self.assertEqual(self.gen_from_csv("\n".join(rows) + "\n"), hub.build_table(lay.partitions()))


class MetaTest(unittest.TestCase):
    def test_round_trip(self):
        slots = [hub.SlotMeta("redsea", "RED SEA", "1.1.0", "Daniil Vlasenko", 428592, 0x1234ABCD), hub.SlotMeta()]
        data = hub.pack_meta(slots)
        self.assertEqual(len(data), hub.META_SIZE)
        self.assertEqual(hub.META_HEAD.size + 2 * hub.META_SLOT.size + 4, 192)  # C: sizeof(meta_t)
        self.assertEqual(hub.unpack_meta(data), slots)

    def test_long_strings_are_cut_with_terminator(self):
        m = hub.unpack_meta(hub.pack_meta([hub.SlotMeta(name="X" * 40), hub.SlotMeta()]))[0]
        self.assertEqual(m.name, "X" * 23)

    def test_blank_or_corrupt_sector_reads_as_empty(self):
        self.assertEqual(hub.unpack_meta(b"\xFF" * hub.META_SIZE), [hub.SlotMeta(), hub.SlotMeta()])
        data = bytearray(hub.pack_meta([hub.SlotMeta(name="A"), hub.SlotMeta()]))
        data[20] ^= 1
        self.assertEqual(hub.unpack_meta(bytes(data)), [hub.SlotMeta(), hub.SlotMeta()])


class ImageTest(unittest.TestCase):
    def test_plain_image(self):
        info = hub.inspect_image(fake_image(5000))
        self.assertEqual(info.problems, [])
        self.assertEqual((info.project, info.app_version, info.idf_version), ("demo", "1.0", "v5.5"))
        self.assertIsNone(info.desc)

    def test_descriptor_is_found(self):
        info = hub.inspect_image(fake_image(5000, desc=("redsea", "RED SEA", "1.1.0", "Daniil")))
        self.assertEqual((info.desc.id, info.desc.name, info.desc.version, info.desc.author),
                         ("redsea", "RED SEA", "1.1.0", "Daniil"))

    def test_wrong_chip_and_garbage(self):
        self.assertTrue(hub.inspect_image(fake_image(5000, chip_id=9)).problems)
        self.assertTrue(hub.inspect_image(b"\0" * 5000).problems)

    def test_too_big(self):
        self.assertTrue(hub.inspect_image(fake_image(hub.APP_END - hub.APP_START)).problems)

    def test_crc_is_zlib(self):
        data = fake_image(4096)
        self.assertEqual(hub.slot_meta_for(data, hub.inspect_image(data),
                                           type("A", (), dict(name=None, id=None, fw_version=None, author=None,
                                                             file="x/demo.bin"))()).image_crc32, zlib.crc32(data))


def checker_cover():
    """64x32 test pattern: a 1-pixel frame and a diagonal."""
    rows = [[x in (0, 63) or y in (0, 31) or x == 2 * y for x in range(64)] for y in range(32)]
    out = bytearray(hub.COVER_BYTES)
    for y, row in enumerate(rows):
        for x, on in enumerate(row):
            if on:
                out[y * 8 + x // 8] |= 0x80 >> (x % 8)
    return bytes(out), rows


class CoverTest(unittest.TestCase):
    def setUp(self):
        self.bits, self.rows = checker_cover()

    def write(self, name, data):
        d = tempfile.mkdtemp()
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_block_round_trip_and_crc(self):
        sector = hub.pack_meta([hub.SlotMeta(name="A", image_size=1), hub.SlotMeta()], [self.bits, None])
        self.assertEqual(len(sector), hub.META_SIZE)
        self.assertEqual(hub.unpack_covers(sector), [self.bits, None])
        self.assertEqual(hub.unpack_meta(sector)[0].name, "A")      # covers don't disturb the slot info
        broken = bytearray(sector)
        broken[hub.COVER_OFFSETS[0] + 100] ^= 1
        self.assertEqual(hub.unpack_covers(bytes(broken)), [None, None])

    def test_cover_inside_firmware(self):
        data = fake_image(6000)
        data = data[:4000] + hub.FW_COVER_MAGIC + self.bits + data[4000 + 12 + 256:]
        self.assertEqual(hub.inspect_image(data).cover, self.bits)

    def test_pbm_p1_and_p4(self):
        # PBM: 1 = black, lit pixels are the white ones.
        p1 = "P1\n# test\n64 32\n" + "\n".join(" ".join("0" if on else "1" for on in row) for row in self.rows)
        self.assertEqual(hub.load_cover(self.write("c.pbm", p1.encode())), self.bits)
        inverted = bytes(b ^ 0xFF for b in self.bits)
        self.assertEqual(hub.load_cover(self.write("c4.pbm", b"P4\n64 32\n" + inverted)), self.bits)

    def test_raw_and_invert(self):
        path = self.write("c.bin", self.bits)
        self.assertEqual(hub.load_cover(path), self.bits)
        self.assertEqual(hub.load_cover(path, invert=True), bytes(b ^ 0xFF for b in self.bits))

    def test_multiples_and_wrong_sizes(self):
        k = 3
        px = [[(255 if self.rows[y // k][x // k] else 0, 255) for x in range(64 * k)] for y in range(32 * k)]
        self.assertEqual(hub.pixels_to_cover(64 * k, 32 * k, px), self.bits)
        with self.assertRaises(ValueError):
            hub.pixels_to_cover(100, 50, [[(0, 255)] * 100] * 50)
        with self.assertRaises(ValueError):
            hub.pixels_to_cover(128, 32, [[(0, 255)] * 128] * 32)

    def test_png_matches_pillow(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        base = Image.new("L", (128, 64))
        for y in range(64):
            for x in range(128):
                base.putpixel((x, y), 255 if self.rows[y // 2][x // 2] else 0)
        variants = {
            "1bit": base.convert("1"),
            "gray": base,
            "rgb": base.convert("RGB"),
            "rgba": base.convert("RGBA"),
            "la": base.convert("LA"),
            "palette": base.convert("P"),
        }
        gray16 = Image.new("I;16", (128, 64))
        gray16.putdata([v * 257 for v in base.getdata()])   # real 16-bit white is 65535
        variants["gray16"] = gray16
        for name, img in variants.items():
            with self.subTest(name):
                path = os.path.join(tempfile.mkdtemp(), f"{name}.png")
                img.save(path, optimize=False)
                self.assertEqual(hub.load_cover(path), self.bits)

    def test_transparent_is_dark(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        img = Image.new("RGBA", (64, 32), (255, 255, 255, 0))
        img.putpixel((5, 5), (255, 255, 255, 255))
        path = os.path.join(tempfile.mkdtemp(), "t.png")
        img.save(path)
        bits = hub.load_cover(path)
        self.assertEqual(sum(bin(b).count("1") for b in bits), 1)
        self.assertEqual(bits[5 * 8], 0x80 >> 5)


if __name__ == "__main__":
    unittest.main()


def png_of(bits):
    """1-bit grayscale PNG of a cover (same bytes per row as the cover)."""
    raw = b"".join(b"\0" + bits[y * 8:(y + 1) * 8] for y in range(32))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 32, 1, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def with_cover(image, bits):
    return image + b"\0" * (-len(image) % 4) + hub.FW_COVER_MAGIC + bits


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.bits, _ = checker_cover()
        self.manifest = {"espidi": 1, "sketch": "Demo", "firmware": "espidi/Demo.ino.bin", "cover": "cover.png",
                         "summary": {"en": "demo"}}
        self.put("cover.png", png_of(self.bits))
        self.put("espidi.json", hub.json.dumps(self.manifest).encode())
        os.makedirs(os.path.join(self.dir, "Demo"))

    def put(self, rel, data):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def build(self, version, bits=None):
        return with_cover(fake_image(6000, desc=("demo", "DEMO", version, "Me")), bits or self.bits)

    def test_manifest_codes(self):
        ok = {"espidi": 1, "firmware": "espidi/a.bin"}
        self.assertEqual(hub.check_manifest(ok), [])
        cases = [([], ["object"]), ({**ok, "espidi": 2}, ["format"]), ({**ok, "espidi": True}, ["format"]),
                 ({"espidi": 1}, ["firmware"]), ({**ok, "firmware": "../a.bin"}, ["firmware"]),
                 ({**ok, "firmware": "/a.bin"}, ["firmware"]), ({**ok, "cover": "c.jpg"}, ["cover"]),
                 ({**ok, "summary": {"en": 1}}, ["summary"]), ({**ok, "sketch": "a b"}, ["sketch"]),
                 ({**ok, "name": "N" * 24}, ["text"]), ({**ok, "version": 1}, ["text"])]
        for m, want in cases:
            self.assertEqual(hub.check_manifest(m), want, m)

    def test_specs(self):
        self.assertEqual(str(hub.parse_spec("https://github.com/o/r/tree/beta/fw/x")), "o/r/fw/x@beta")
        self.assertEqual(str(hub.parse_spec("o/r/fw@feature/x")), "o/r/fw@feature/x")
        self.assertEqual(str(hub.parse_spec("https://github.com/o/r.git")), "o/r")
        for bad in ("o", "https://github.com/o/r/issues", "o/r/../x", "o/r@a b"):
            self.assertRaises(hub.RepoError, hub.parse_spec, bad)

    def test_not_published_yet(self):
        rep = hub.check_repo(hub.LocalRepo(self.dir))
        self.assertEqual(rep.cover, self.bits)
        self.assertTrue(any("not in the repository" in p for p in rep.problems))
        self.assertEqual(hub.check_repo(hub.LocalRepo(self.dir), need_firmware=False).problems, [])

    def test_sync_cover_writes_a_compilable_header(self):
        path = hub.sync_cover(self.dir, self.manifest, self.bits)
        with open(path) as f:
            text = f.read()
        self.assertIn("ESPIDI_COVER(", text)
        self.assertTrue(all(line.startswith("//") for line in text.split("#pragma once")[0].splitlines()))

    def test_publish_only_a_new_version_or_cover(self):
        rep = hub.check_repo(hub.LocalRepo(self.dir), need_firmware=False)
        self.assertTrue(hub.publish_build(self.dir, rep, self.build("1.0")))
        rep = hub.check_repo(hub.LocalRepo(self.dir))
        self.assertEqual((rep.problems, rep.warnings, rep.meta().version), ([], [], "1.0"))
        self.assertFalse(hub.publish_build(self.dir, rep, self.build("1.0")))
        self.assertTrue(hub.publish_build(self.dir, rep, self.build("1.0", bytes(256))))
        rep = hub.check_repo(hub.LocalRepo(self.dir))
        self.assertTrue(any("differs" in w for w in rep.warnings))
        self.assertTrue(hub.publish_build(self.dir, rep, self.build("1.1")))

    def test_firmware_without_descriptor_needs_a_name(self):
        self.put("espidi/Demo.ino.bin", fake_image(6000))
        self.assertTrue(any("no ESPIDI_FIRMWARE" in p for p in hub.check_repo(hub.LocalRepo(self.dir)).problems))
        self.put("espidi.json", hub.json.dumps({**self.manifest, "name": "OLD FW", "version": "2"}).encode())
        rep = hub.check_repo(hub.LocalRepo(self.dir))
        self.assertEqual((rep.problems, rep.meta().name, rep.meta().version), ([], "OLD FW", "2"))

    def test_template_is_an_espidi_ready_repository(self):
        rep = hub.check_repo(hub.LocalRepo(os.path.join(ROOT, "templates", "arduino-firmware")))
        self.assertEqual((rep.problems, rep.warnings), ([], []))
        self.assertEqual(rep.info.cover, rep.cover)
