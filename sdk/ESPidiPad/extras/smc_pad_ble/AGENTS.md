<!-- From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md. -->

# AGENTS.md: SMC-PAD Pro BLE kit

You are working with a kit that describes how a stock M-VAVE SMC-PAD Pro talks over Bluetooth LE, so that an ESP32
(ESPidi, `sdk/ESPidiPad`, NimBLE central) can read the pad's controls and drive its LEDs. The pad's firmware is not
changed by anything here.

**Source of truth:** `PROTOCOL.md`. Every fact there carries a provenance tag ([firmware], [app], [capture],
[smc-pad], [open]). The reference implementation is `ref/smc_pad_protocol.py`; `esp32/SmcPadProtocol.h` must give the
same bytes (checked by `esp32/test/test_protocol.cpp` against `vectors.json`).

## Hard rules

1. **Attribution and provenance.** Where code or docs built on this kit go public, credit it as `NOTICE.md` asks:
   the pi32v2_re project for [firmware] facts, spectalive/smc-pad (Apache-2.0) for [app], [capture] and [smc-pad]
   facts. Keep the provenance tag when you copy a fact into your own docs, so a reader can tell what was seen on a
   device from what was read from code.
2. **Never send to the pad**:
   - any write, or command `0x21`, to region 0 (raw flash);
   - any write to region 3 (calibration);
   - save0 (`addr 0, len 0`) to any region, except region 5 on an explicit user action (it rewrites 28 KiB of flash);
   - region 5 at address `0x80000000`;
   - on USB, a SysEx ending `… 7D F7` (it reboots the pad into its ROM loader).
   The builders refuse these (`Refused` in Python, `E_REFUSED` / `E_RANGE` in C++). Do not bypass them, and do not
   build packets by hand.
3. **One request in flight.** Send the next config packet only after the reply on `AE42`, with a timeout and a
   resend: the pad drops packets silently while busy and answers nothing to a bad packet.
4. **Bounds.** Every region-5 access stays inside `0x6E98` bytes. The pad does not check; the builders do.
5. **Read before you write.** Read region 4 and region 5 at connect; write pad records whole, from bytes read from
   the pad; do not assume the factory defaults (section 2.3 of PROTOCOL.md).
6. **[open] facts are not facts yet.** Do not build a feature that depends on one without a check on the user's pad,
   and report what you measured (see `OPEN_QUESTIONS.md`).
7. **No firmware work on the pad.** No ROM mode, no flashing, no dumps: this kit is for the host side only.

## Changing the protocol code

- Any new packet builder goes into `ref/smc_pad_protocol.py` first, with a test; then `python ref/make_vectors.py`;
  then the same builder in `esp32/SmcPadProtocol.h`, which must pass `esp32/test`.
- Never edit `vectors.json` or `esp32/test/vectors.h` by hand.

## Done means

1. `cd ref && python -m unittest -v test_smc_pad_protocol` passes.
2. `cd esp32/test && c++ -std=c++11 -Wall -Wextra -I.. test_protocol.cpp -o test_protocol && ./test_protocol`
   passes without warnings.
3. Your firmware build passes with no warnings from your code.
4. Then ask the user to confirm on hardware, and report the result. Do not claim a pad behaviour you have not seen.
