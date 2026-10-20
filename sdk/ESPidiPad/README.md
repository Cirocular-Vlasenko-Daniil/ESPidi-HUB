# ESPidiPad

Русская версия: [README.ru.md](README.ru.md).

An **M-VAVE SMC-PAD Pro** over Bluetooth LE as a controller for an ESPidi firmware: its 16 pads (8 banks), 8 encoders and 5 buttons arrive as events in `loop()`, and its 128 RGB pad LEDs can be set. The pad's own firmware is not changed — it stays a normal SMC-PAD for its app and other hosts.

Built on the **SMC-PAD Pro BLE kit** by Ruslan Mazavin (pi32v2_re), in [`extras/smc_pad_ble/`](extras/smc_pad_ble/) unchanged: the protocol ([PROTOCOL.md](extras/smc_pad_ble/PROTOCOL.md)), its reference implementation and the C++ packet builder this library uses. Credits below.

## Quick start

Needs Arduino-ESP32 **3.3 or newer** (its BLE library runs on NimBLE on the ESP32-C3). Copy `sdk/ESPidiPad` (and `sdk/ESPidiHub`) into your Arduino `libraries` folder.

```cpp
#include <ESPidiHub.h>
#include <ESPidiPad.h>

espidi::Pad pad;

void setup() {
  pad.begin();                       // reconnects to the remembered pad by itself
}

void loop() {
  espidi::PadEvent e;
  while (pad.read(e)) {
    switch (e.kind) {
      case espidi::PadEvent::PAD:     // e.index 0-127 = e.bank() * 16 + e.pad(); e.value = velocity, 0 = released
        if (e.value) pad.setColor(e.index, 255, 40, 0);
        break;
      case espidi::PadEvent::ENCODER: // e.index 0-15, e.value, e.delta
        break;
      case espidi::PadEvent::BUTTON:  // e.index 0-4, e.value 1 = pressed
        break;
      default:                        // MIDI, CONNECTED, READY, DISCONNECTED, PAIRED, PAIR_FAILED
        break;
    }
  }
}
```

Start from [`examples/PadLab`](examples/PadLab/PadLab.ino): pairing, every event on the OLED, LEDs, and a report for the bench.

## Pairing

Once per board: put the pad into Bluetooth pairing mode (see its manual), then call `pad.pair()` — e.g. on a button. The library scans for 30 s, checks the pad's identity (`SMC-PAD_…`), pairs with just-works bonding and remembers the pad in the NVS namespace **`espidi_pad`**, shared by every firmware on the board: after that any firmware with ESPidiPad connects to it with a plain `begin()`. `pad.forget()` — or **FORGET PAD** in the HUB menu — clears it.

`state()`: `NO_PAD` → `PAIRING` → `CONNECTING` → `SYNCING` (`progress()` 0-100) → `READY`.

## Events

Every control's message is configured in the pad's active profile, and the library reads that profile at connect (PROTOCOL.md 2.2) — so it knows that, say, note 36 on channel 10 is pad 0 and CC 30 is encoder 0, whatever MidiSuite set.

| `kind` | `index` | `value` | `delta` |
|---|---|---|---|
| `PAD` | pad record 0-127 (`bank()`, `pad()`) | velocity, 0 = released | |
| `ENCODER` | encoder record 0-15 | what it sent | change; relative encoders ±1 |
| `BUTTON` | 0-4 | 1 pressed, 0 released (trigger and toggle types: press only) | |
| `MIDI` | | | |

Every event carries the original message in `midi[]` / `len`, so a firmware can also just pass MIDI through. Until the profile is read (`READY`) everything arrives as `MIDI`. Expect bursts from encoders: the pad sends every step of a fast turn [firmware].

## LEDs

`setColor(pad, r, g, b)`, `fillBank(bank, r, g, b)`, `getColor()`. Colours live in the pad's RAM: they show at once and wear no flash [firmware, capture]. The calls only record the wish; the background task sends one request at a time (the pad drops anything sent while it is busy), latest colour first, and a whole bank in one packet when four or more of its pads changed. Colours the firmware set are put back after a reconnect or a profile switch on the pad.

## What is not settled yet

The kit marks some facts [open] until Ruslan's bench confirms them (its [OPEN_QUESTIONS.md](extras/smc_pad_ble/OPEN_QUESTIONS.md)). The library does not depend on them:

- **Connect time.** MidiSuite reads all 28 KiB of profiles before writing (31 round trips), and so does the library by default (`PadOptions::fullSync = true`). If the bench shows it isn't needed, `fullSync = false` reads only the active profile (6 round trips).
- **Pairing**: whether the pad needs a bond, its address type, MTU and connection interval — PAD LAB prints them.
- **Pads**: whether they send velocity or a fixed value — `value` is whatever the pad sent.
- **Encoder records 8-15** (the pad has 8 knobs): reported by record number.
- **Colours after power-off**: probably lost; the library repaints after reconnecting.

## Rules this library keeps (and you must too)

From the kit's [AGENTS.md](extras/smc_pad_ble/AGENTS.md), enforced by `SmcPadProtocol.h`:

- nothing is ever written to the pad's flash: no region 0, no calibration (region 3), no `save0` — the library has no call for it;
- one config request in flight, with a timeout and resend; every access bounds-checked;
- records are written whole, from bytes read from the pad.

Don't talk to the pad's config service (`AE40`) yourself, and don't build packets by hand: add what you need to the library, following the kit's "Changing the protocol code".

## Size

The BLE stack and the library add about 330 KB of flash: PAD LAB builds to 715 KB (a 768 KB slot), the template without BLE to 388 KB. It fits next to most firmwares.

## Tests

```bash
python3 -m unittest discover -s tools/tests       # the kit's checks + PadCore.h against a fake pad
```

`extras/test/test_padcore.cpp` runs `PadCore.h` (connect sequence, resends, LEDs, profile switch, event mapping) against a fake pad on a computer; the bytes it expects come from the kit's Python reference (`make_padcore_vectors.py`) — the full sync is the captured MidiSuite session byte for byte. Nothing here has run against a real pad yet: PAD LAB is for that.

## Credits

- **SMC-PAD Pro BLE kit** — Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8, from the **pi32v2_re** project: facts tagged [firmware] (the pad's firmware `SMC-PAD_004`, read from a full flash dump) and `SmcPadProtocol.h`. The kit may be used in public projects; the licence of its own files is not set yet.
- **spectalive/smc-pad** — https://github.com/spectalive/smc-pad, Copyright 2026 Cristian Deluxe, Apache-2.0: facts tagged [app], [capture] and [smc-pad], and the captured byte sequences used as test vectors.
- **Quixotic7/MvaveFM1Unbricker** — https://github.com/Quixotic7/MvaveFM1Unbricker: the ROM-mode command the kit warns about.

M-VAVE and SMC-PAD are names of their owner. No M-VAVE code or firmware is included.
