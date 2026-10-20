# From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md.
"""
smc_pad_protocol.py: the reference implementation of the SMC-PAD Pro config protocol (PROTOCOL.md, section 3) and of
the BLE-MIDI stream it sends (section 2). Pure Python 3, no dependencies, no I/O: it builds and parses bytes, and an
ESP32 implementation can be checked against it with vectors.json (make_vectors.py).

    from smc_pad_protocol import *
    pkt = color_write(profile=0, pad=3, rgb=(255, 0, 0))     # bytes for AE41
    rx = Reassembler(); rx.feed(notify_bytes) -> list of complete packets
"""
from dataclasses import dataclass

HDR = b"\x00\x59"
MAX_PACKET = 0x416                 # len + 7 must not exceed this (1,046 bytes)
MAX_DATA = MAX_PACKET - 7          # 1,039
REGION5_SIZE = 0x6E98              # 28,312 bytes: 8 profiles x 3,539
PROFILE_SIZE = 0xDD3               # 3,539
BUTTON_OFF, BUTTON_SIZE, BUTTONS = 0x000, 23, 5
ENCODER_OFF, ENCODER_SIZE, ENCODERS = 0x073, 6, 16
PAD_OFF, PAD_SIZE, PADS = 0x0D3, 26, 128
PAD_RGB = 5                        # offset of R, G, B inside a pad record
REGION4_SIZE = 12
READ_CHUNK = 1009                  # MidiSuite's read size (0x3F1)
WRITE_CHUNK = 135                  # MidiSuite's BLE write size (data bytes per logical packet)

CMD_OK, CMD_IDENTITY, CMD_CONST, CMD_MIDI = 0x00, 0x11, 0x12, 0x15
CMD_OP0, CMD_WRITE, CMD_READ, CMD_OP3 = 0x21, 0x22, 0x23, 0x24

# GATT (PROTOCOL.md 1.1). Discover by UUID; the handles are this firmware's.
SVC_CONFIG = "0000AE40-0000-1000-8000-00805F9B34FB"
CHR_CONFIG_WRITE = "0000AE41-0000-1000-8000-00805F9B34FB"      # write without response
CHR_CONFIG_NOTIFY = "0000AE42-0000-1000-8000-00805F9B34FB"     # notify
SVC_MIDI = "03B80E5A-EDE8-4B33-A751-6CE34EC4C700"
CHR_MIDI = "7772E5DB-3868-4112-A1A9-F2669D106BF3"             # read, write without response, notify
HANDLES = {"AE41": 0x62, "AE42": 0x64, "AE42_CCC": 0x65, "MIDI": 0x72, "MIDI_CCC": 0x73}


class Refused(ValueError):
    """A request the driver rules forbid (PROTOCOL.md section 5)."""


def checksum(data):
    return (~sum(data)) & 0xFF


def packet(cmd, data=b""):
    """A logical packet: 00 59 cmd len24 data checksum."""
    data = bytes(data)
    if len(data) > MAX_DATA:
        raise ValueError(f"{len(data)} data bytes: the pad drops packets over {MAX_DATA}")
    return HDR + bytes([cmd]) + len(data).to_bytes(3, "little") + data + bytes([checksum(data)])


@dataclass
class Packet:
    cmd: int
    data: bytes

    @property
    def ok(self):
        """A write's acknowledgement: cmd 00 with first data byte 0."""
        return self.cmd == CMD_OK and self.data[:1] == b"\x00"


def parse(buf):
    """One complete logical packet -> Packet; raises ValueError on a bad header, length or checksum."""
    buf = bytes(buf)
    if len(buf) < 7 or buf[:2] != HDR:
        raise ValueError("not a 00 59 packet")
    n = int.from_bytes(buf[3:6], "little")
    if len(buf) != n + 7:
        raise ValueError(f"length field says {n + 7} bytes, got {len(buf)}")
    data = buf[6:6 + n]
    if buf[-1] != checksum(data):
        raise ValueError(f"checksum {buf[-1]:02x}, expected {checksum(data):02x}")
    return Packet(buf[2], data)


class Reassembler:
    """Rebuilds logical packets from a byte stream cut into notification (or write) pieces, as the pad does with
    writes (PROTOCOL.md 3.4) and as a host must with replies (3.5). Bytes outside a packet are discarded."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, piece):
        self.buf += piece
        out = []
        while True:
            i = self.buf.find(HDR)
            if i < 0:
                del self.buf[:max(0, len(self.buf) - 1)]     # keep a trailing 00 that may start a header
                return out
            del self.buf[:i]
            if len(self.buf) < 6:
                return out
            total = int.from_bytes(self.buf[3:6], "little") + 7
            if total > MAX_PACKET:                            # not a packet the pad would send: resync
                del self.buf[:2]
                continue
            if len(self.buf) < total:
                return out
            raw = bytes(self.buf[:total])
            del self.buf[:total]
            try:
                out.append(parse(raw))
            except ValueError:
                pass


# ---------------------------------------------------------------------------------------------------- requests

def _hdr(region, addr, length):
    if not 0 <= region <= 7:
        raise ValueError("region 0-7")
    return bytes([region]) + (addr & 0xFFFFFFFF).to_bytes(4, "little") + length.to_bytes(3, "little")


def _guard(region, addr, length, write):
    """The driver rules (PROTOCOL.md section 5) as code."""
    if region == 0:
        raise Refused("region 0 is the raw flash")
    if region == 3 and write:
        raise Refused("region 3 is calibration: never written")
    if region not in (3, 4, 5):
        raise Refused(f"region {region} is not installed in this firmware (no reply)")
    if write and addr == 0 and length == 0:
        raise Refused("save0 (addr 0, len 0) writes flash: use save_profiles(), with the user's consent")
    if region == 5 and addr & 0x80000000:
        raise Refused("region 5 at 0x80000000 (active-profile handling) is not traced")
    size = {3: 0xA0, 4: REGION4_SIZE, 5: REGION5_SIZE}[region]
    if addr + length > size:
        raise Refused(f"region {region}: {addr:#x} + {length} is past its {size} bytes (the pad does not check)")


def identity():
    return packet(CMD_IDENTITY)


def read(region, addr, length):
    _guard(region, addr, length, write=False)
    if length > MAX_DATA - 8:
        raise ValueError(f"read at most {MAX_DATA - 8} bytes per request (MidiSuite uses {READ_CHUNK})")
    return packet(CMD_READ, _hdr(region, addr, length))


def write(region, addr, data):
    data = bytes(data)
    _guard(region, addr, len(data), write=True)
    if len(data) + 8 > MAX_DATA:
        raise ValueError(f"write at most {MAX_DATA - 8} bytes per request")
    return packet(CMD_WRITE, _hdr(region, addr, len(data)) + data)


def save_profiles(user_consented=False):
    """Region 5 save0: erases 7 flash sectors and writes 28,312 bytes. Flash wear: only on a user's request."""
    if not user_consented:
        raise Refused("save0 rewrites 28 KiB of flash: pass user_consented=True only for an explicit user action")
    return packet(CMD_WRITE, _hdr(5, 0, 0))


def midi_to_pad(msg):
    """Command 0x15: MIDI bytes into the pad's MIDI queue (destination not traced yet)."""
    return packet(CMD_MIDI, bytes(msg))


def connect_sequence():
    """The requests MidiSuite sends at connect, each after the previous reply (PROTOCOL.md 3.6): 31 packets."""
    out = [identity(), read(4, 0, REGION4_SIZE)]
    for a in range(0, REGION5_SIZE, READ_CHUNK):
        out.append(read(5, a, min(READ_CHUNK, REGION5_SIZE - a)))
    return out


def read_reply(p, region, addr, length):
    """The bytes of a 0x23 reply after checking that it answers this request (the 8-byte header is echoed)."""
    if p.cmd != CMD_READ or p.data[:8] != _hdr(region, addr, length) or len(p.data) != 8 + length:
        raise ValueError("not the reply to this read")
    return p.data[8:]


# ---------------------------------------------------------------------------------------------------- addresses

def button_addr(profile, b):
    return profile * PROFILE_SIZE + BUTTON_OFF + b * BUTTON_SIZE


def encoder_addr(profile, e):
    return profile * PROFILE_SIZE + ENCODER_OFF + e * ENCODER_SIZE


def pad_addr(profile, i):
    if not (0 <= profile < 8 and 0 <= i < PADS):
        raise ValueError("profile 0-7, pad 0-127")
    return profile * PROFILE_SIZE + PAD_OFF + i * PAD_SIZE


def color_write(profile, pad, rgb):
    """One pad's colour, RAM only (PROTOCOL.md 4.2)."""
    return write(5, pad_addr(profile, pad) + PAD_RGB, bytes(rgb))


def bank_colors(records, colors):
    """16 pad records (416 bytes, read from the pad) with bytes [5..7] replaced: the data for one write."""
    records = bytearray(records)
    if len(records) != 16 * PAD_SIZE or len(colors) != 16:
        raise ValueError("16 records of 26 bytes and 16 colours")
    for k, (r, g, b) in enumerate(colors):
        records[k * PAD_SIZE + PAD_RGB: k * PAD_SIZE + PAD_RGB + 3] = bytes((r, g, b))
    return bytes(records)


# ---------------------------------------------------------------------------------------------------- what records send

def button_messages(rec, pressed, toggle_state=False):
    """The MIDI a button record sends on a press or a release (PROTOCOL.md 2.2)."""
    t, ch, num, off, on = rec[0], rec[1] & 0x0F, rec[2], rec[3], rec[4]
    if t == 0:
        return [bytes([0xB0 | ch, num, off])] if pressed else []
    if t == 1:
        return [bytes([0xB0 | ch, num, on if not toggle_state else off])] if pressed else []
    if t == 2:
        return [bytes([0xB0 | ch, num, on if pressed else off])]
    if t == 3:
        return [bytes([0xB0 | ch, 0x00, num]), bytes([0xB0 | ch, 0x20, off]), bytes([0xC0 | ch, on])] if pressed else []
    if t == 4:
        return [bytes(rec[6:6 + rec[5]])] if pressed else []
    if t == 5:
        return [bytes([0x90, num, 0x7F if pressed else 0x00])]      # always channel 1, velocity 127
    return []


def default_profile():
    """The record image the firmware builds when no config is saved (PROTOCOL.md 2.3). Bytes not set by the
    firmware's defaults builder are zero here [firmware: the builder's other writes are not all traced]."""
    p = bytearray(PROFILE_SIZE)
    for b in range(BUTTONS):
        o = BUTTON_OFF + b * BUTTON_SIZE
        p[o] = 4
        p[o + 5] = 6
        p[o + 6:o + 12] = bytes([0xF0, 0x7F, 0x7F, 0x06, b + 1, 0xF7])
        p[o + 0x16] = 0xFF
    for e in range(ENCODERS):
        o = ENCODER_OFF + e * ENCODER_SIZE
        p[o:o + 6] = bytes([0x00, 0x02, 0x00, 30 + e, 0x00, 0x7F])
    for i in range(PADS):
        o = PAD_OFF + i * PAD_SIZE
        p[o + 1], p[o + 2], p[o + 4] = 0x09, (4 + i) & 0xFF, 0x7F
        p[o + 5:o + 8] = bytes([0xFF, 0x6E, 0xFF])
        p[o + 8] = 0xFF
    return bytes(p)


# ---------------------------------------------------------------------------------------------------- BLE-MIDI in

def ble_midi_messages(pkt):
    """Splits one BLE-MIDI notification into (timestamp13, message) pairs: header, then per message a timestamp
    byte (bit 7 set) and the message; running status allowed; SysEx kept whole (standard BLE-MIDI framing)."""
    pkt = bytes(pkt)
    if len(pkt) < 3 or not pkt[0] & 0x80:
        return []
    hi = (pkt[0] & 0x3F) << 7
    out, i, status = [], 1, None
    lengths = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}
    while i < len(pkt):
        ts = hi | (pkt[i] & 0x7F) if pkt[i] & 0x80 else None
        if pkt[i] & 0x80:
            i += 1
            if i >= len(pkt):
                break
        if pkt[i] & 0x80:
            status = pkt[i]
            i += 1
        if status is None:
            break
        if status == 0xF0:
            # data bytes until F7, which BLE-MIDI precedes with a timestamp byte (a SysEx spanning several
            # notifications continues in the next one without a status byte; not handled here)
            body = bytearray([0xF0])
            while i < len(pkt):
                b = pkt[i]
                if b < 0x80:
                    body.append(b)
                    i += 1
                elif b == 0xF7 or (i + 1 < len(pkt) and pkt[i + 1] == 0xF7):
                    body.append(0xF7)
                    i += 1 if b == 0xF7 else 2
                    break
                else:
                    i += 1
            out.append((ts, bytes(body)))
            status = None
            continue
        if status >= 0xF8:
            out.append((ts, bytes([status])))
            continue
        n = lengths.get(status & 0xF0, 0)
        out.append((ts, bytes([status]) + pkt[i:i + n]))
        i += n
    return out


# ---------------------------------------------------------------------------------------------------- USB form

def usb_sysex(logical):
    """The logical packet as the USB SysEx MidiSuite sends (LSB-first 8-to-7 bit packing)."""
    out, acc, bits = [0xF0], 0, 0
    for b in logical:
        acc |= b << bits
        bits += 8
        while bits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            bits -= 7
    if bits:
        out.append(acc & 0x7F)
    return bytes(out + [0xF7])


def from_usb_sysex(sysex):
    sysex = bytes(sysex)
    if sysex[:1] != b"\xF0" or sysex[-1:] != b"\xF7":
        raise ValueError("not F0 ... F7")
    out, acc, bits = bytearray(), 0, 0
    for b in sysex[1:-1]:
        acc |= (b & 0x7F) << bits
        bits += 7
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return bytes(out)
