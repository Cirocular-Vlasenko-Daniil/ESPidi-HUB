# From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md.
"""Tests of smc_pad_protocol.py against bytes captured from real pads and the MidiSuite app
(spectalive/smc-pad, Apache-2.0: reference/FINDINGS.md, reference/gatt_unlock.txt).

    python -m unittest -v test_smc_pad_protocol
"""
import unittest

import smc_pad_protocol as P

h = bytes.fromhex

# reference/gatt_unlock.txt: the first 31 requests of the captured BLE session that unlocked colour writes
UNLOCK = """00 59 11 00 00 00 FF
00 59 23 08 00 00 04 00 00 00 00 0C 00 00 EF
00 59 23 08 00 00 05 00 00 00 00 F1 03 00 06
00 59 23 08 00 00 05 F1 03 00 00 F1 03 00 12
00 59 23 08 00 00 05 E2 07 00 00 F1 03 00 1D
00 59 23 08 00 00 05 D3 0B 00 00 F1 03 00 28
00 59 23 08 00 00 05 C4 0F 00 00 F1 03 00 33
00 59 23 08 00 00 05 B5 13 00 00 F1 03 00 3E
00 59 23 08 00 00 05 A6 17 00 00 F1 03 00 49
00 59 23 08 00 00 05 97 1B 00 00 F1 03 00 54
00 59 23 08 00 00 05 88 1F 00 00 F1 03 00 5F
00 59 23 08 00 00 05 79 23 00 00 F1 03 00 6A
00 59 23 08 00 00 05 6A 27 00 00 F1 03 00 75
00 59 23 08 00 00 05 5B 2B 00 00 F1 03 00 80
00 59 23 08 00 00 05 4C 2F 00 00 F1 03 00 8B
00 59 23 08 00 00 05 3D 33 00 00 F1 03 00 96
00 59 23 08 00 00 05 2E 37 00 00 F1 03 00 A1
00 59 23 08 00 00 05 1F 3B 00 00 F1 03 00 AC
00 59 23 08 00 00 05 10 3F 00 00 F1 03 00 B7
00 59 23 08 00 00 05 01 43 00 00 F1 03 00 C2
00 59 23 08 00 00 05 F2 46 00 00 F1 03 00 CE
00 59 23 08 00 00 05 E3 4A 00 00 F1 03 00 D9
00 59 23 08 00 00 05 D4 4E 00 00 F1 03 00 E4
00 59 23 08 00 00 05 C5 52 00 00 F1 03 00 EF
00 59 23 08 00 00 05 B6 56 00 00 F1 03 00 FA
00 59 23 08 00 00 05 A7 5A 00 00 F1 03 00 05
00 59 23 08 00 00 05 98 5E 00 00 F1 03 00 10
00 59 23 08 00 00 05 89 62 00 00 F1 03 00 1B
00 59 23 08 00 00 05 7A 66 00 00 F1 03 00 26
00 59 23 08 00 00 05 6B 6A 00 00 F1 03 00 31
00 59 23 08 00 00 05 5C 6E 00 00 3C 00 00 F4""".splitlines()


class Packets(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(P.identity(), h("00 59 11 00 00 00 FF"))
        self.assertEqual(P.usb_sysex(P.identity()), h("F0 00 32 45 00 00 00 40 7F F7"))

    def test_connect_sequence_is_the_captured_unlock(self):
        self.assertEqual([p.hex(" ").upper() for p in P.connect_sequence()], UNLOCK)

    def test_region4_read_usb(self):
        self.assertEqual(P.usb_sysex(P.read(4, 0, 12)),
                         h("F0 00 32 0D 41 00 00 00 02 00 00 00 00 40 01 00 00 6F 01 F7"))

    def test_color_write_captured(self):
        pkt = P.color_write(0, 3, (0xFF, 0, 0))
        self.assertEqual(pkt, h("00 59 22 0B 00 00 05 26 01 00 00 03 00 00 FF 00 00 D1"))
        self.assertEqual(P.usb_sysex(pkt), h("F0 00 32 09 59 00 00 40 02 26 02 00 00 30 00 00 00 7F 01 00 08 0D F7"))
        # the colour write that closed the captured BLE unlock session: pad record 32 (note 36), black
        self.assertEqual(P.color_write(0, 32, (0, 0, 0)), h("00 59 22 0B 00 00 05 18 04 00 00 03 00 00 00 00 00 DB"))

    def test_save0_is_guarded_and_matches_the_app(self):
        with self.assertRaises(P.Refused):
            P.save_profiles()
        pkt = P.save_profiles(user_consented=True)
        self.assertEqual(pkt, h("00 59 22 08 00 00 05 00 00 00 00 00 00 00 FA"))
        self.assertEqual(P.usb_sysex(pkt), h("F0 00 32 09 41 00 00 40 02 00 00 00 00 00 00 00 00 7A 01 F7"))


class Replies(unittest.TestCase):
    def test_ok_frame(self):
        logical = P.from_usb_sysex(h("F0 00 32 01 08 00 00 00 00 7F 01 F7"))
        self.assertEqual(logical, h("00 59 00 01 00 00 00 FF"))
        self.assertTrue(P.parse(logical).ok)

    def test_region4_reply(self):
        p = P.parse(h("00 59 23 14 00 00 04 00 00 00 00 0C 00 00 78 00 32 04 00 00 01 01 03 01 01 00 3A"))
        self.assertEqual(P.read_reply(p, 4, 0, 12), h("78 00 32 04 00 00 01 01 03 01 01 00"))
        with self.assertRaises(ValueError):
            P.read_reply(p, 5, 0, 12)

    def test_bad_checksum_and_length(self):
        with self.assertRaises(ValueError):
            P.parse(h("00 59 00 01 00 00 00 FE"))
        with self.assertRaises(ValueError):
            P.parse(h("00 59 22 0B 00 00 00 05 26 01 00 00 03 00 00 FF 00 00 D1"))   # the 4-byte-length form

    def test_reassembler(self):
        ok = h("00 59 00 01 00 00 00 FF")
        rd = h("00 59 23 14 00 00 04 00 00 00 00 0C 00 00 78 00 32 04 00 00 01 01 03 01 01 00 3A")
        r = P.Reassembler()
        stream = b"\x13\x37" + ok + rd + ok
        got = []
        for k in range(0, len(stream), 5):          # 5-byte notifications
            got += r.feed(stream[k:k + 5])
        self.assertEqual([g.cmd for g in got], [0x00, 0x23, 0x00])
        self.assertEqual(got[1].data[8:], h("78 00 32 04 00 00 01 01 03 01 01 00"))


class Guards(unittest.TestCase):
    def test_refusals(self):
        for call in (lambda: P.read(0, 0, 4), lambda: P.write(0, 0, b"\x00"), lambda: P.write(3, 1, b"\x00"),
                     lambda: P.write(5, 0x6E98 - 2, b"abc"), lambda: P.read(5, 0x80000000, 1),
                     lambda: P.read(6, 0, 1), lambda: P.write(4, 10, b"abc")):
            with self.assertRaises(P.Refused):
                call()

    def test_limits(self):
        with self.assertRaises(ValueError):
            P.packet(0x15, bytes(P.MAX_DATA + 1))
        self.assertEqual(len(P.read(5, 0, P.READ_CHUNK)), 15)          # a read request is always 8 + 7 bytes
        with self.assertRaises(ValueError):
            P.read(5, 0, P.MAX_DATA - 7)                                # its reply would exceed 1,046 bytes


class Records(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(P.pad_addr(0, 3) + P.PAD_RGB, 0x126)
        self.assertEqual(P.pad_addr(0, 32) + P.PAD_RGB, 0x418)
        self.assertEqual(P.pad_addr(7, 127) + P.PAD_SIZE, P.REGION5_SIZE)
        self.assertEqual(P.encoder_addr(0, 0), 0x73)

    def test_default_records_send(self):
        d = P.default_profile()
        b0 = d[P.button_addr(0, 0):P.button_addr(0, 0) + P.BUTTON_SIZE]
        self.assertEqual(P.button_messages(b0, True), [h("F0 7F 7F 06 01 F7")])        # MMC Stop
        self.assertEqual(P.button_messages(b0, False), [])
        pad = d[P.pad_addr(0, 0) - 0:P.pad_addr(0, 0) + P.PAD_SIZE]
        self.assertEqual((pad[1], pad[2], pad[5:8]), (9, 4, h("FF 6E FF")))
        enc = d[P.encoder_addr(0, 2):P.encoder_addr(0, 2) + 6]
        self.assertEqual(enc, h("00 02 00 20 00 7F"))

    def test_note_button_is_channel_1(self):
        self.assertEqual(P.button_messages(bytes([5, 9, 40, 0, 127]), True), [h("90 28 7F")])

    def test_bank(self):
        recs = bytes(range(256)) + bytes(160)
        out = P.bank_colors(recs, [(1, 2, 3)] * 16)
        self.assertEqual(out[5:8], h("01 02 03"))
        self.assertEqual(out[0:5], recs[0:5])
        self.assertEqual(len(P.write(5, P.pad_addr(0, 0), out)), 416 + 8 + 7)


class BleMidi(unittest.TestCase):
    def test_captured_note(self):
        self.assertEqual(P.ble_midi_messages(h("80 80 99 16 7F")), [(0, h("99 16 7F"))])

    def test_running_status_and_burst(self):
        msgs = P.ble_midi_messages(h("81 82 B0 1E 10 83 1E 11 1E 12"))
        self.assertEqual([m for _, m in msgs], [h("B0 1E 10"), h("B0 1E 11"), h("B0 1E 12")])
        self.assertEqual(msgs[0][0], (1 << 7) | 2)

    def test_sysex(self):
        self.assertEqual(P.ble_midi_messages(h("80 80 F0 7F 7F 06 01 80 F7")), [(0, h("F0 7F 7F 06 01 F7"))])


if __name__ == "__main__":
    unittest.main()
