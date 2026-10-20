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


if __name__ == "__main__":
    unittest.main()
