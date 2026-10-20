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
- **NVS namespaces:** each firmware uses its descriptor `id`. Reserved: `espidihub` (HUB), `espidi_pad` (the paired SMC-PAD, shared by all firmwares, see "Bluetooth controller"), `nimble_bond` (NimBLE bonds).
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

Written by the installer, read by the HUB (`hub/main/slots.c`). Little-endian. Slot info: 192 bytes at 0; cover blocks at `0x100` (slot 1) and `0x300` (slot 2); the rest of the 4 KB sector `0xFF`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `ESPIDIHB` |
| 8 | 2 | format = 1 |
| 10 | 2 | reserved = 0 |
| 12 | 88 × 2 | slot 1, slot 2 |
| 188 | 4 | CRC-32 (zlib) of bytes 0–187 |

Slot record (88 bytes): `id[16]`, `name[24]`, `version[16]`, `author[24]` — NUL-terminated ASCII; `image_size` u32 (0 = empty); `image_crc32` u32.

A sector with a wrong magic, format or CRC reads as "no metadata". The HUB then takes the name from the firmware descriptor, then from the app description, and falls back to `SLOT n`.

### Cover block

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `ESPIDICV` |
| 8 | 2 | width = 64 |
| 10 | 2 | height = 32 |
| 12 | 4 | reserved = 0 |
| 16 | 256 | pixels |
| 272 | 4 | CRC-32 (zlib) of bytes 0–271 |

Pixels: 1 bit each, rows top to bottom, 8 bytes per row, most significant bit = leftmost pixel, 1 = lit — the Adafruit GFX `drawBitmap()` format (image2cpp: "Horizontal – 1 bit per pixel"). A block that is blank or fails the check means "no cover stored"; the HUB then looks for a cover inside the firmware, and without one shows the name alone. The blocks sit outside the 192 bytes covered by the slot-info CRC, so devices installed before covers existed read as "no cover", and the format number stays 1.

The installer stores the cover given on the command line, else the one inside the firmware (so the HUB reads 276 bytes instead of scanning the image). The composer does the same, plus catalog covers and pictures the user uploads.

### Start screen

When the HUB starts a slot it shows the cover at x 0–63 and, from x 66, the name split into up to two lines of 10 characters and the version (10 characters), centred vertically with 9-pixel lines; without a cover, the name centred, at double size if it fits. The screen holds 600 ms with a cover, 300 ms without, and stays on the OLED until the firmware draws. `web/app.js` `drawStart()` draws the same for the preview.

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

### Firmware cover

Optional, put into a firmware by `ESPIDI_COVER(...)` from the SDK (`tools/espidi_hub.py cover picture.png > cover.h` writes it). 268 bytes, 4-byte aligned, `used, retain`: magic `ESPIDI-CV-1\0` (12 bytes), then the 256 bytes of pixels in the cover-block format. The HUB scans the first 1 MB of a slot for it in 4-byte steps when the metadata sector has no cover for that slot.

## Install procedures (`tools/espidi_hub.py`)

- **base** — `erase_flash`, then bootloader `0x0`, empty partition table, empty metadata, HUB `0x10000`. The only operation that erases settings.
- **install** — read table and metadata, compute the new layout, write the image at its new offset, then the new table and metadata (with the cover: `--cover`, else the firmware's own; `--no-cover` for none; the other slot's cover stays). If interrupted before the table is written, the old table still describes the device and a half-written slot fails validation, so the HUB starts.
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
- **Catalog:** `web/catalog/catalog.json` with the binaries next to it (GitHub release downloads are not CORS-enabled, so Pages serves them). `tools/update_catalog.py` fills sizes and SHA-256 and syncs entries that have a `source` (an ESPidi-ready repository) — firmware, cover, name, version, summary, commit; the site verifies the checksum after downloading.
- **GitHub tab:** loads a firmware live from an ESPidi-ready repository (below), `github.js`. Share links: `?play=<spec>&page=<spec>` load repositories into the slots; the address bar follows the GitHub slots. The last six repositories are remembered in `localStorage`.
- **Covers:** per slot — the firmware's own (catalog `cover` PNG, the repository's `cover`, else the one inside the image, else on an update the one already on the board), a picture the user uploads, or none. 64×32 and exact multiples are converted pixel for pixel exactly as `espidi_hub.py cover` does (checked by `test.html`); any other size is scaled to fit and dithered (Atkinson), which only the site does. Changing only the cover of a kept firmware rewrites just the table and the metadata sector.
- The preview draws the HUB menu and both start screens with the HUB's own font (`font.js`, generated from `font.c`).

## ESPidi-ready repositories

A firmware repository becomes installable from a link when it has `espidi.json` (at the root or in a folder):

| Key | Type | |
|---|---|---|
| `espidi` | `1` | required; the format number |
| `firmware` | path to a `.bin` | required; the app image committed in the repository |
| `cover` | path to a `.png` | 64×32 or an exact multiple |
| `sketch` | folder name | for the workflow and `repo --sync-cover` |
| `summary` | text or `{lang: text}` | |
| `id` ≤ 15, `name` ≤ 23, `version` ≤ 15, `author` ≤ 23 | text | fallbacks for images without the descriptor |

Paths are relative to `espidi.json`, `/`-separated, without `.`/`..`/empty segments. Problem codes (same in `check_manifest()` and `validateManifest()`): `object`, `format`, `firmware`, `cover`, `summary`, `sketch`, `text`.

**Spec** of a repository: `owner/repo[/folder][@ref]`, a `github.com/owner/repo[/tree|blob/<ref>/<folder>]` link, or a `raw.githubusercontent.com/owner/repo/<ref>/<folder>/espidi.json` link. A `/tree/` link takes the first segment after `tree` as the ref; a branch with `/` in its name needs the `@` form.

**Loading** (`github.js`, `GithubRepo` in Python): resolve the ref to a commit through the GitHub API (`GET /repos/{o}/{r}/commits/{ref}`, `Accept: application/vnd.github.sha`; 404 → no repository, 422 → no such ref; on rate limit or network failure the files are read by the ref name), then read `espidi.json`, the firmware and the cover of that commit from `raw.githubusercontent.com`, falling back to jsDelivr (`cdn.jsdelivr.net/gh/{o}/{r}@{sha}/…`) when raw is unreachable. A 404 is final. Release assets are not used: they are served without CORS headers.

**Publishing** (`templates/arduino-firmware/.github/workflows/firmware.yml`): `repo . --sync-cover` → build → `repo . --build BIN` → `repo . --build BIN --publish` copies the build to `firmware` when there is none yet, or its descriptor version or embedded cover differs from the committed one (builds of the same version differ only by timestamps, so that would commit on every push) → the workflow commits and pushes it with the workflow token (such commits don't trigger workflows).

## Bluetooth controller (`sdk/ESPidiPad`)

A stock M-VAVE SMC-PAD Pro as a controller for firmwares; the protocol is the SMC-PAD Pro BLE kit (Ruslan Mazavin, pi32v2_re), kept unchanged in `sdk/ESPidiPad/extras/smc_pad_ble/` — its PROTOCOL.md is the reference.

- **In the firmware, not in the HUB:** only one app runs at a time, so the HUB cannot hold a connection for a firmware. An optional library, so firmwares without a pad don't carry the BLE stack (~330 KB). It uses Arduino-ESP32's BLE library on NimBLE (central), no other dependency.
- **Shared pairing:** `pair()` scans for a pad in pairing mode, checks its identity reply (`SMC-PAD…`), pairs with just-works bonding and stores the pad in NVS `espidi_pad` (`addr` = 6 bytes in NimBLE order, `type`, `id`). Bonds are in NimBLE's `nimble_bond`. Every firmware then reconnects with `begin()`. The HUB menu shows FORGET PAD while `espidi_pad/addr` exists and erases that namespace on confirmation (the HUB has no BLE); the next `pair()` deletes the old bond of that address first.
- **Two halves:** `PadCore.h` (no BLE, no heap, host-tested) holds the session — identity → region 4 → region 5 (all 28 KiB as MidiSuite does while the kit's bench question 1 is open, or only the active profile with `fullSync = false`), one request in flight (800 ms timeout, 4 s for 1,009-byte reads, 3 tries, then reconnect), a region-4 poll every 2 s to follow profile switches on the pad, LED wishes coalesced (latest colour wins; four or more in a bank → one 431-byte bank write of whole records) — and maps the pad's MIDI to pad / encoder / button events through the active profile. `ESPidiPad.cpp` is the BLE half: a FreeRTOS task (connect with a 4 s cancel timer, GATT discovery, AE42 and BLE-MIDI notifications, writes split to MTU−3), a stream buffer for AE42 bytes, a queue of MIDI messages and state events for `loop()`, a mutex around the session.
- **Rules:** packets come only from the kit's `SmcPadProtocol.h`, which refuses region 0, writes to region 3, `save0` and out-of-range access; the library has no call that writes the pad's flash.
- **Not seen on a device yet:** everything above is from the kit and the fake-pad tests. `examples/PadLab` prints what the kit's OPEN_QUESTIONS.md asks for (identity, address type, security, MTU, interval, time to ready, records).

## Planned
- Settle the kit's open questions on a real pad (PAD LAB), then decide `fullSync`'s default.
