<!-- From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md. -->

# M-VAVE SMC-PAD Pro over Bluetooth LE: the protocol in both directions

This is the reference for a host that talks to a stock SMC-PAD Pro over BLE: what the pad sends, and what a host may
send to it. The intended host is an ESP32 acting as BLE central (ESPidi, `sdk/ESPidiPad`). Nothing here requires
changing the pad's firmware.

**Firmware described:** identity `SMC-PAD_004`, chip JieLi BR25 (AC636N), read from a full flash dump and traced
statically. A unit with another firmware (spectalive's pad reported `SMC-PAD_003`) may differ in details. Where
sources disagree, the firmware wins.

**Provenance tags** on every fact:

| tag | source |
|---|---|
| [firmware] | static analysis of the pad's own firmware (pi32v2_re); read from code, not yet seen on a device |
| [app] | the MidiSuite app, decompiled by spectalive/smc-pad (Apache-2.0) |
| [capture] | traffic captured from a real pad by spectalive/smc-pad |
| [smc-pad] | behaviour spectalive observed on their pad |
| [open] | not settled yet; bench results will follow (`OPEN_QUESTIONS.md`) |

Byte sequences are hex, multi-byte fields little-endian unless said otherwise.

---

## 1. Connection

### 1.1 GATT database [firmware]

Read from the firmware's ATT database (36 entries). Handles are fixed in this firmware, but **discover by UUID**, not
by handle.

| handle | entry | properties |
|---|---|---|
| `0x01` | service `1800` GAP: `2A00` device name (`0x03`), `2A01` appearance (`0x05`), `2A04` preferred connection parameters (`0x07`) | read |
| `0x08` | service `1801` GATT: `2A05` service changed (`0x0a`, CCC `0x0b`) | indicate |
| `0x0c` | service `180A` Device Information: `2A29` manufacturer (`0x0e`), `2A24` model (`0x10`) | read |
| `0x1f` | service `180F` Battery: `2A19` battery level (`0x21`, CCC `0x22`) | read, notify |
| `0x60` | **service `AE40`: the config protocol (section 3)** | |
| `0x62` | `AE41`: host → pad | **write without response only** |
| `0x64` | `AE42`: pad → host (replies), CCC `0x65` | notify |
| `0x70` | **service `03B80E5A-EDE8-4B33-A751-6CE34EC4C700`: BLE-MIDI (section 2)** | |
| `0x72` | `7772E5DB-3868-4112-A1A9-F2669D106BF3`: MIDI I/O, CCC `0x73` | read, write without response, notify |
| `0x80` | service `AE00`: the vendor SDK's "trans_data" demo profile | |
| `0x82` | `AE01` (host → pad) | write without response |
| `0x84` | `AE02` (pad → host), CCC `0x85` | notify |

- No characteristic carries an ATT permission bit that requires encryption or authentication [firmware]. Whether
  the pad nevertheless asks for pairing on connect is [open].
- `AE00` is not the config protocol: writes to it go to a handler registered at run time that was not traced
  [firmware]. **Use `AE40`.**
- Because `AE41` has no write-with-response, the only acknowledgement of a config packet is its reply on `AE42`
  (section 3.5).

### 1.2 Advertising and bonding [smc-pad]

- Seen from macOS: the pad advertises only while it is in Bluetooth pairing mode. Once bonded, it did not show in a
  scan, and the host had to reconnect to the known peripheral instead.
- For ESPidi this matches the planned design: discover the pad once in pairing mode, store its address (NVS
  `espidi_pad`), then connect directly.
- Address type (public or random), whether a bond is required, the ATT MTU the pad accepts and the connection
  interval are [open].

### 1.3 Transports at once [smc-pad]

The pad sends on every transport it has open: with USB plugged in, button presses still arrive over BLE-MIDI. The
config protocol is the same packet format on USB (wrapped in SysEx, section 3.8) and on BLE (raw on `AE41`).

---

## 2. Pad → host: MIDI over BLE-MIDI

### 2.1 Transport

- Subscribe to notifications on `7772E5DB-…` (write `01 00` to its CCC).
- Packets are standard BLE-MIDI: a header byte (bit 7 set, high 6 bits of a 13-bit timestamp), then for each message
  a timestamp byte (bit 7 set, low 7 bits) and the MIDI message; running status may follow within a packet.
  Captured example [capture]: `80 80 99 16 7F` = header `80`, timestamp `80`, NoteOn channel 10, note `0x16`,
  velocity 127.
- The pad's BLE-MIDI characteristic is also writable: MIDI written to it enters the pad's MIDI queue, the same queue
  as config command `0x15` [firmware]. Where that queue goes (USB out, the 3.5 mm port) is [open]. **Plain notes and
  CCs sent to the pad do not light its LEDs** [smc-pad: every note on every channel tried]. LED colours go through
  the config protocol (section 4).

### 2.2 What each control sends

What the pad sends is **configured, not fixed**: every control has a record in the active user profile (region 5,
section 4.3), and the host can read that record to know what a control will send. The tables below give the
record's meaning [firmware]; section 2.3 gives the factory defaults.

**Controls**: 16 RGB pads in 8 banks (128 pad records), 8 encoders (16 encoder records), 5 buttons
(`<`, `>` and three edge buttons; in the defaults the transport buttons).

**Buttons** (5 records × 23 bytes). Fields: `[0]` type, `[1]` channel (0-15), `[2]` CC or note, `[3]` off value,
`[4]` on value, `[5]` raw-message length, `[6..]` raw message bytes. A press is event 1, a release events 0 and 3.

| `[0]` | type | on press | on release |
|---|---|---|---|
| 0 | CC trigger | `B0+ch [2] [3]` | — |
| 1 | CC toggle | alternately `[4]` and `[3]` (state kept per button) | — |
| 2 | CC momentary | `B0+ch [2] [4]` | `B0+ch [2] [3]` |
| 3 | program change with bank | `B0+ch 00 [2]`, `B0+ch 20 [3]`, `C0+ch [4]` | — |
| 4 | raw message | `[5]` bytes from `[6..]` (the defaults: MMC SysEx) | — |
| 5 | note | `90 [2] 7F` | `90 [2] 00` |
| > 5 | nothing | | |

The note type ignores `[1]`: its status byte is hardcoded `90`, so it always sends on channel 1 with velocity 127
[firmware].

**Encoders** (16 records × 6 bytes; the pad has 8 knobs, the use of records 8-15 is [open]). Fields: `[0]` type,
`[1]` acceleration table (0-3), `[2]` channel, `[3]` CC, `[4]` min, `[5]` max.

| `[0]` | type | sends |
|---|---|---|
| 0 | CC absolute | `B0+ch [3] value` |
| 1 | channel pressure | `D0+ch value` |
| 2 | pitch bend, Mackie style | `E0+ch 00 value`, and a touch note `0x68+ch` on, sent off after 100 idle ticks |
| 3 | CC relative | `B0+ch [3] [5]` per step forward, `B0+ch [3] [4]` per step back |
| other | nothing | |

- Types 0-2 move a 16-bit target value, clamped to `[4]..[5]` (either order). A periodic routine moves the sent value
  one step per tick toward the target and **sends every step**, so a fast turn arrives as a run of consecutive
  values, not a jump [firmware]. Expect bursts.
- Type 3 sends one message per accelerated step. The codes are whatever `[4]`/`[5]` hold: `7F`/`01` gives two's
  complement, `3F`/`41` offset binary [firmware].
- Acceleration: the detent count per poll (clamped to ±4) goes through table `[1]`: table 0 does not accelerate,
  tables 1-3 multiply a fast turn by up to 3-4 [firmware].

**Pads** (128 records × 26 bytes, 8 banks of 16). Known fields: `[1]` channel, `[2]` note, `[4]` = `7F` in the
defaults, `[5..7]` RGB colour, `[8]` = `FF` [firmware, app]. The pads' handler and their type field were not traced:
whether pads send velocity or a fixed value, and what `[0]`, `[3]` and `[9..25]` mean, is [open].
**PAD BANK** moves the surface up one bank of 16 records [smc-pad].

### 2.3 Factory defaults [firmware]

What the firmware builds when it finds no saved config. A pad that was ever configured with MidiSuite holds that
configuration instead (spectalive's: pads on channel 10 from note 36, buttons CC 25-29, encoders CC 30-37 [smc-pad]).
**Do not hardcode either: read region 5.**

| controls | default record | sends |
|---|---|---|
| buttons 1-5 | `[0]=4`, `[5]=6`, `[6..11]=F0 7F 7F 06 n F7`, n = 1…5 | MMC Stop, Play, Deferred Play, Fast Forward, Rewind |
| encoders 0-15 | `00 02 00 <30+i> 00 7F` | CC 30+i absolute, channel 1, acceleration table 2 |
| pads 0-127 | `[1]=09`, `[2]=04+i`, `[4]=7F`, RGB `FF 6E FF`, `[8]=FF` | notes 4…131 on channel 10 (record i = note 4+i) |

---

## 3. Host → pad: the config protocol on `AE40`

### 3.1 Packet [firmware, app, capture]

```
00 59 <cmd> <len: 3 bytes LE> <data: len bytes> <checksum>        total len + 7 bytes
checksum = (~(sum of the data bytes)) & 0xFF                        header bytes are not summed
```

- Identity request: `00 59 11 00 00 00 FF` (no data, checksum `FF`).
- **The length field is 3 bytes.** A decompile of the Android app shows a builder with a 4-byte length
  (`00 59 22 0B 00 00 00 …`) [app]. The firmware checks `len + 7 == bytes received` [firmware], so that form would be
  dropped, and the captured working BLE session uses 3 bytes [capture]. Use 3.

### 3.2 Commands [firmware]

| cmd | data | what the pad does | reply |
|---|---|---|---|
| `0x11` | none | identity | `0x11`, 27 bytes: `SMC-PAD_004` padded with zeros |
| `0x12` | none | nothing | constant `00 59 12 06 00 00 00 00 00 00 00 00 FF` |
| `0x15` | MIDI bytes | queues them as MIDI (as BLE-MIDI input); destination [open] | OK |
| `0x21` | region, addr | region operation 0 (in region 0: **erase a 4 KiB flash sector**) | OK |
| `0x22` | region, addr, len, bytes | write | OK |
| `0x23` | region, addr, len | read | `0x23` with the data (3.5) |
| `0x24` | region, addr, len | region operation 3, returns 16 bits | 18 bytes |
| other | | **nothing, no reply** | — |

For `0x21`-`0x24` the data starts with a header of 8 bytes: `[0]` region (must be ≤ 7), `[1..4]` address (32-bit),
`[5..7]` length (24-bit); a write's bytes follow. A missing region, an unknown command, a bad checksum or a wrong
length all get **no reply at all** [firmware].

### 3.3 Regions [firmware]

| region | what | read | write |
|---|---|---|---|
| 0 | **raw SPI flash** (the firmware-update path) | flash read | **flash write; op 0 erases a sector. Never.** |
| 3 | calibration (`trimdat`, 160 bytes in RAM) | yes | **never**: `addr 0, len 0` rewrites its flash sector |
| 4 | globals, 12 bytes | yes | memcpy, then the settings are applied; byte 10 = active user profile; saved to flash by the pad itself after ~150 ticks |
| 5 | user profiles, `0x6E98` = 28,312 bytes (8 × 3,539) | yes | RAM memcpy, effective at once (LED colours show at once); see the save rule below |
| 1, 2, 6, 7 | not installed | no reply | no reply |

Region 5 write special cases:
- `addr 0, len 0` ("save0" in MidiSuite) erases 7 sectors and writes all 28,312 bytes to flash: **the only path from
  region 5 to flash**. Flash wear: never in a loop. [firmware, app]
- `addr 0x80000000, len 1`: active-profile handling, not traced. **Do not use.** [firmware]
- **No bounds check**: `addr + len > 0x6E98` overwrites whatever RAM follows the table, silently, and the read does
  not check either. The host must check. [firmware]

### 3.4 Transport rules [firmware]

- **Reassembly.** The pad rebuilds one logical packet from several GATT writes. A write shorter than 7 bytes, or one
  not starting with `00 59`, is dropped unless a packet is already being assembled. A packet with `len + 7 > 0x416`
  is dropped: **the largest packet is 1,046 bytes** (1,039 data bytes).
- **Busy drop.** Writes are dropped without any error while the pad is busy: while its previous reply has not been
  handed to notify yet, while a USB SysEx transfer is in progress, or while the previous packet is still being
  processed. Since `AE41` is write-without-response, the host sees nothing; only the missing reply tells.
- USB and BLE share that busy state: a MidiSuite session on USB blocks BLE writes.

### 3.5 Replies on `AE42` [firmware, capture]

- A reply is a packet of the same format, sent in pieces over notifications; **reassemble by the header**: wait for
  `00 59`, read the 3-byte length, collect `len + 7` bytes, check the checksum.
- OK: `00 59 00 01 00 00 00 FF` (cmd `00`, one data byte `00`). A write succeeded when that byte is 0 [app].
- Read reply: cmd `0x23`, data = the request's 8-byte header echoed, then the bytes. Captured region-4 read
  [capture]: `00 59 23 14 00 00 04 00 00 00 00 0C 00 00 78 00 32 04 00 00 01 01 03 01 01 00 3A`, the 12 globals being
  `78 00 32 04 00 00 01 01 03 01 01 00`.

### 3.6 Connecting the way MidiSuite does [app, capture]

1. Subscribe to `AE42`.
2. `0x11` identity, wait for the reply.
3. Read region 4: `00 59 23 08 00 00 04 00 00 00 00 0C 00 00 EF`.
4. Read all of region 5 in 1,009-byte pieces (28 full reads and one of 60 bytes), each after the previous reply.
5. Then writes are accepted; MidiSuite polls region 4 periodically and re-reads region 5 when the active profile
   changes.

spectalive found writes ignored until this whole sequence had run on the connection, and called it a "session"
[smc-pad]. The firmware has **no unlock state** [firmware]: the likely explanation is that the sequence works because
it waits for each reply, so nothing is sent while the pad is busy. Whether step 4 can be skipped is the first bench
item [open]. Until then, run the full sequence; it costs 31 round trips.

### 3.7 Chunking

- MidiSuite splits BLE writes into logical packets of up to 135 data bytes each, every one a complete `0x22` packet
  with its own address [app]. Reads go in 1,009-byte pieces [app].
- A logical packet larger than the ATT payload is split across several GATT writes; the pad reassembles them
  (3.4). The pad's MTU is [open]; plan for 20-byte writes as the fallback.

### 3.8 The same protocol over USB [app, capture]

On USB the logical packet goes as SysEx on the `SMC-PAD-Private` port: `F0`, then the packet bit-packed LSB-first into
7-bit bytes (`acc |= byte << bits; bits += 8; while bits >= 7: emit acc & 0x7F; acc >>= 7; bits -= 7`, then the
remaining bits), then `F7`. Identity becomes `F0 00 32 45 00 00 00 40 7F F7`. **Never send `F0 22 24 35 7D F7`: it
reboots the pad into the ROM loader** [firmware; the same command on the FM-1 is public: github.com/Quixotic7/MvaveFM1Unbricker].

---

## 4. Recipes

### 4.1 Read the configuration

Region 4 (12 bytes) for the active profile (byte 10), region 5 for the records. Record addresses, profile `p`:

| record | address in region 5 |
|---|---|
| button `b` (0-4) | `p*3539 + b*23` |
| encoder `e` (0-15) | `p*3539 + 0x73 + e*6` |
| pad `i` (0-127) | `p*3539 + 0xD3 + i*26` |

### 4.2 Set a pad's colour (RAM only, no flash wear)

Write 3 bytes at `p*3539 + 0xD3 + i*26 + 5`. The colour shows at once [smc-pad, capture]; it is lost at power-off
unless saved [firmware; to confirm, open].

```
00 59 22 0B 00 00 05 <addr 4 bytes> 03 00 00 R G B <checksum>
```

Captured example [capture]: profile 0, pad record 3, red → addr `0x126`:
`00 59 22 0B 00 00 05 26 01 00 00 03 00 00 FF 00 00 D1`.

**A whole bank** (16 consecutive pad records, 416 bytes) fits in one packet: 416 + 8 + 7 = 431 ≤ 1,046. Take the
records from a region-5 read and change only bytes `[5..7]` of each [firmware]. LED animation speed is limited only by
the round trip (one request in flight).

### 4.3 Remap a control

Write its whole record (4.1) in region 5. It takes effect at once and lasts until power-off; saving needs save0
(3.3), which rewrites 28 KiB of flash: let the user decide, not a loop.

---

## 5. Rules for a host driver

1. **Keep one request in flight.** Send the next packet only after the reply. Use a timeout (the pad may drop or
   ignore a packet without any reply) and resend.
2. **Bounds-check every region-5 access yourself:** `addr + len ≤ 0x6E98`.
3. **Never send:** any write or op `0x21` to region 0; any write to region 3; save0 in a loop or without the user;
   region 5 at `0x80000000`; SysEx `… 7D F7` on USB.
4. **Write records whole**, starting from bytes read from the pad.
5. **Do not assume the defaults**: read the profile at connect.
6. **Expect encoder bursts** (2.2) and notes on channel 1 from note-type buttons.
7. **Treat a missing reply as normal** (busy, unknown command, bad length): it is how the pad signals everything.

## 6. Sources

- [firmware]: pi32v2_re, `docs/hardware/smc_pad_pro.md` (2026-10-15), the pad's flash dump traced with a pi32v2
  disassembler that matches the vendor's byte for byte, and the GATT database parsed from `app.bin`
  (`0x1E16B50`-`0x1E16CEE`).
- [app], [capture], [smc-pad]: https://github.com/spectalive/smc-pad (Cristian Deluxe, Apache-2.0):
  `reference/FINDINGS.md`, `docs/smc-pad-led.md`, `README.md`, `reference/gatt_unlock.txt`. Its `reference/mac-decompile/`
  is M-VAVE's code and not licensed for reuse.
