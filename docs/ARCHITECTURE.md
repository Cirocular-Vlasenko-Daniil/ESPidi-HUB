# ESPidi HUB — Architecture

How the HUB boots, where everything lives in flash, how slots are sized, and the binary formats the installer, the HUB and the SDK share. Read this before changing the HUB itself; for writing a firmware, see [FIRMWARE_GUIDE.md](FIRMWARE_GUIDE.md).

## Components

| Part | Where | Changes |
|---|---|---|
| Second-stage bootloader | `hub/bootloader_components/main/` | rarely — a bad bootloader needs a USB re-flash |
| HUB app (factory partition) | `hub/main/` | splash, button choice, menu |
| Flash layout | `hub/partitions.csv` + `tools/espidi_hub.py` | **a contract** — see below |
| Installer | `tools/espidi_hub.py` | sizes slots, writes firmwares |
| SDK | `sdk/ESPidiHub/` | what a firmware links |

The ROM bootloader of the ESP32-C3 is in mask ROM and cannot be changed. It always loads the second-stage bootloader from flash offset `0x0`; that one is ours.

## Boot

```
power-on ─► ROM ─► ESPidi bootloader ─┬─ power-on / brown-out ─────────────► HUB
                                      └─ any other reset ─► otadata slot ──► firmware
                                                             (image invalid ─► HUB)
HUB: splash ─┬─ PLAY held ─► slot 1 ┐
             ├─ PAGE held ─► slot 2 ├─► esp_ota_set_boot_partition() + esp_restart()
             ├─ nothing   ─► last   ┘         (otadata written only when it changes)
             └─ ENC held  ─► menu
```

- **Bootloader rule** (`espidi_hub_select()`): reset reason `RESET_REASON_CHIP_POWER_ON` (on the C3 this also covers the EN pin and chip-level brown-out) or `RESET_REASON_SYS_BROWN_OUT` → factory index. Otherwise the stock otadata choice, after an `esp_image_verify()` of that slot: an invalid slot boots the HUB instead of the stock fallback (which would try the other slot).
- **Consequences:** power cycle and the board's RESET button → HUB. `esp_restart()`, panics and watchdogs inside a firmware → the same firmware again, no splash. Deep-sleep wake → the same firmware.
- **HUB start after a software reset with otadata pointing at a slot** can only mean the bootloader refused that slot; the HUB then shows `SLOT n BROKEN` and opens the menu instead of auto-starting anything (no boot loop).
- **Back to the HUB from a firmware:** `espidi::rebootToHub()` = `esp_ota_set_boot_partition(hub)` + `esp_restart()`.
- **Last used slot:** NVS namespace `espidihub`, key `last` (u8, 0/1), written only when it changes.
- **NVS namespaces:** each firmware uses its descriptor `id`. Reserved: `espidihub` (HUB), `espidi_pad` (pairing shared by all firmwares, see Planned), `nimble_bond` (NimBLE bonds).
- **TAP (GPIO2) is not used at boot**: it is a strapping pin, the chip may not boot if it is held LOW at reset.

## Flash layout (4 MB)

| Name | Type / subtype | Offset | Size | Notes |
|---|---|---|---|---|
| bootloader | — | `0x0` | ≤ 32 KB | ESPidi bootloader |
| partition table | — | `0x8000` | 3 KB | rewritten on every install |
| `nvs` | data / nvs | `0x9000` | 16 KB | shared by all firmwares, never erased by the HUB or the installer |
| `espidi` | `0x40` / `0x00` | `0xD000` | 4 KB | slot metadata, see below |
| `otadata` | data / ota | `0xE000` | 8 KB | which slot a software reset starts |
| `hub` | app / factory | `0x10000` | 256 KB | HUB app |
| `slot1` | app / ota_0 | `0x50000` | dynamic | starts at `0x50000`, grows up |
| (free gap) | | | | |
| `slot2` | app / ota_1 | dynamic | dynamic | ends at `0x300000`, grows down |
| `spiffs` | data / spiffs | `0x300000` | 1 MB | shared LittleFS |

The app area is `0x50000`–`0x300000`, 2 752 KB.

**This layout is a contract.** Every installed device and every firmware relies on it. Moving `nvs`, `spiffs` or the app area means a full erase on every device. `hub/partitions.csv` (the empty layout) and `empty_layout()` in `tools/espidi_hub.py` must stay identical; CI builds the HUB and compares the two binaries byte for byte.

## Dynamic slots

- Slot 1 is anchored at `APP_START = 0x50000`, slot 2 at `APP_END = 0x300000`; the free space is the gap between them. Installing into one slot therefore never moves the other: the installer checks the gap and rewrites both table entries.
- Allocation = image size rounded up to 64 KB (app partitions must be 64 KB aligned).
- An empty slot keeps a 64 KB placeholder entry at its anchor, so the table always has `ota_0` and `ota_1` (otadata maps sequence numbers to slots modulo the number of OTA partitions — that number must never change).
- Largest slot: `2 752 KB − allocation of the other slot` (placeholder counts as 64 KB) → **2.62 MB** when the other slot is empty.
- Free space shown by the HUB and the installer: `2 752 KB − slot1 − slot2` (empty slots count as 0).

## `espidi` metadata sector

Written by the installer, read by the HUB (`hub/main/slots.c`). Little-endian, 192 bytes, rest of the 4 KB sector `0xFF`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `ESPIDIHB` |
| 8 | 2 | format = 1 |
| 10 | 2 | reserved = 0 |
| 12 | 88 × 2 | slot 1, slot 2 |
| 188 | 4 | CRC-32 (zlib) of bytes 0–187 |

Slot record (88 bytes): `id[16]`, `name[24]`, `version[16]`, `author[24]` — NUL-terminated ASCII; `image_size` u32 (0 = empty); `image_crc32` u32.

A sector with a wrong magic, format or CRC reads as "no metadata". The HUB then takes the name from the firmware descriptor, then from the app description, and falls back to `SLOT n`.

## Firmware descriptor

Put into a firmware by `ESPIDI_FIRMWARE(id, name, version, author)` from the SDK; found by scanning the image for the magic. 92 bytes, 4-byte aligned, kept by `__attribute__((used, retain))`.

| Offset | Size | Field |
|---|---|---|
| 0 | 12 | magic `ESPIDI-FW-1\0` |
| 12 | 16 | id — NVS namespace and folder name |
| 28 | 24 | name |
| 52 | 16 | version |
| 68 | 24 | author |

Arduino builds report `arduino-lib-builder` as the project name in the ESP-IDF app description, which is why the descriptor exists.

## Install procedures (`tools/espidi_hub.py`)

- **base** — `erase_flash`, then bootloader `0x0`, empty partition table, empty metadata, HUB `0x10000`. The only operation that erases settings.
- **install** — read table and metadata, compute the new layout, write the image at its new offset, then the new table and metadata. If interrupted before the table is written, the old table still describes the device and a half-written slot fails validation, so the HUB starts.
- **remove** — erase the placeholder area at the slot's anchor first, then write the table and metadata.

## Failure modes

| Situation | Result |
|---|---|
| Slot image corrupt or half-written | bootloader → HUB → `SLOT n BROKEN`, menu |
| No firmware installed | `NO FIRMWARE`, menu |
| No display connected | HUB works blind: buttons and last-used still start firmwares |
| NVS fails to initialise | HUB does not erase it; it just stops remembering the last slot |
| Bootloader or table destroyed | hold BOOT while plugging in (ROM download mode) and run `base` again — the chip cannot be bricked by flash contents |

## Compatibility

- Bootloader built with ESP-IDF v5.5 — the same IDF as Arduino-ESP32 3.3, so its app images boot as they are.
- The installer rejects images whose chip id is not ESP32-C3 and merged images (bootloader + table + app).
- The bootloader uses DIO, which every ESP32-C3 flash supports. Arduino builds default to QIO; this should not matter (an ESP-IDF app sets up its own flash driver), but it is one of the things to confirm on hardware.

## Web composer (`web/`)

Static site on GitHub Pages. `espidi.js` is a port of the installer (layout, partition table, metadata, image checks, MD5, CRC-32); `web/test.html` checks it against vectors produced by `tools/gen_web_vectors.py` — identical bytes, including a whole merged image. `flasher.js` drives the board through Web Serial with esptool-js 0.7.0 (vendored, Apache-2.0).

- **Write — full install:** erase the chip, then bootloader, HUB, slot images, partition table, slot info (the same order as the CLI). Needs the HUB build in `web/hub/` (`manifest.json` + the two binaries).
- **Write — update:** reads the table and slot info from the board; each slot is *keep*, *replace* or *empty*; writes only the changed images, a blank placeholder for an emptied slot, then the table and slot info. NVS and files stay. Slots that are empty in the composition default to *keep* when a board with the HUB is connected, so an update never deletes a firmware by accident.
- **Download:** one image from `0x0` to the end of the firmware area (same bytes as `espidi_hub.py image`); flashing it at `0x0` clears NVS, the file partition is not touched.
- **Catalog:** `web/catalog/catalog.json` with the binaries next to it (GitHub release downloads are not CORS-enabled, so Pages serves them). `tools/update_catalog.py` fills sizes and SHA-256; the site verifies the checksum after downloading.
- The preview draws the HUB menu with the HUB's own font (`font.js`, generated from `font.c`).

## Planned
- BLE controller support (`sdk/ESPidiPad`, first target: the M-VAVE SMC-PAD, based on a reverse-engineered protocol). Lives in the firmware, not in the HUB — only one app runs at a time, so the HUB cannot keep a connection for a firmware. A separate optional library (NimBLE central) so firmwares without a pad don't carry the BLE stack (~200–300 KB). Layers: BLE MIDI transport → device profile (pads/encoders/LEDs) → events delivered in `loop()`. The pad's address is stored once in the NVS namespace `espidi_pad`, so every firmware reconnects without scanning; the HUB menu can offer "forget pad" without needing BLE itself.
