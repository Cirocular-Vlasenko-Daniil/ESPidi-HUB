# AGENTS.md — ESPidi HUB

Instructions for AI coding agents. Two tasks happen in this ecosystem; find yours below.
Humans: the same content with explanations is in [docs/FIRMWARE_GUIDE.md](docs/FIRMWARE_GUIDE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Task A — writing a firmware that runs in an ESPidi HUB slot

### Context

- Board: ESPidi = ESP32-C3 Super Mini, 4 MB flash, SSD1306 128×32 OLED, rotary encoder, 3 buttons, DIN MIDI in/out.
- The HUB owns the bootloader, the partition table and slot selection. Your firmware is a plain app image installed into slot 1 or slot 2.
- A correct firmware does not need to know about the HUB. The SDK (`sdk/ESPidiHub/src/ESPidiHub.h`, header-only) adds a name for the HUB menu, safe file storage and a way back to the HUB.

### Hard rules — every item is mandatory

1. Target `esp32:esp32:esp32c3` (Arduino-ESP32 3.x) or ESP-IDF v5.x with target `esp32c3`.
2. Add exactly one descriptor at file scope:
   `ESPIDI_FIRMWARE("<id>", "<NAME>", "<x.y.z>", "<author>");`
   - `id`: lowercase `[a-z0-9_]`, ≤ 15 chars, unique to this firmware. Reserved, never use: `espidihub`, `espidi_pad`, `nimble_bond`.
   - `name` ≤ 23, `version` ≤ 15, `author` ≤ 23 chars.
3. Settings: `Preferences` (NVS) with namespace = `id`. Nothing else in NVS (`sdk/ESPidiPad` itself keeps `espidi_pad` and NimBLE `nimble_bond` — never touch them directly).
4. Files: only via `espidi::mountFiles()` and only under `"/" + id`; use `espidi::dataDir(id)`.
5. NEVER call: `nvs_flash_erase`, `nvs_flash_erase_partition`, `LittleFS.format`, `LittleFS.begin(true, …)`, `SPIFFS.*`, `FFat.*`, `Update.*`, `esp_ota_begin/write/end`, `esp_https_ota*`, `esp_ota_set_boot_partition` (except through `espidi::rebootToHub()`), `esp_partition_erase_range`, `esp_partition_write`, `spi_flash_*` writes, `esp_flash_write/erase*`.
6. NEVER ship a custom partition table, bootloader or merged image. The deliverable is `<sketch>.ino.bin` only.
7. Use the ESPidi pins below via `espidi::pin::*`. Buttons and encoder: `INPUT_PULLUP`, active LOW.
8. Do not require TAP (GPIO2) to be held at power-on — strapping pin.
9. To return to the HUB, call `espidi::rebootToHub()`. `esp_restart()` restarts your own firmware.
10. Optional cover ([sdk/ESPidiHub/COVERS.md](sdk/ESPidiHub/COVERS.md)): `cover.png` in the repository, 64×32 or an exact multiple, white = lit, no firmware name in the picture. `<SketchFolder>/cover.h` (`ESPIDI_COVER(...)`, included after `<ESPidiHub.h>`) is generated — `espidi_hub.py repo . --sync-cover` — never write the bytes by hand.
11. Make the repository ESPidi-ready (docs/FIRMWARE_GUIDE.md, "Publishing"): `espidi.json` with `"espidi": 1`, `"sketch"`, `"firmware"` (the built `.bin` committed in the repository, e.g. `espidi/<Sketch>.ino.bin`), `"cover"`, `"summary"`; copy the template's `.github/workflows/firmware.yml`, which publishes a build whenever the version in `ESPIDI_FIRMWARE()` changes. To publish a new version, change that version string.
12. Bluetooth controller (M-VAVE SMC-PAD Pro): only through `sdk/ESPidiPad` (`espidi::Pad`, README there). Never write to the pad's config service (`AE40`/`AE41`) or build its packets yourself, never add a call that writes the pad's flash (`save0`, regions 0 and 3). Changing the pad code itself: follow `sdk/ESPidiPad/extras/smc_pad_ble/AGENTS.md` (the kit's rules: Python reference first, vectors, then C++), and do not claim pad behaviour you have not seen — ask the user to run `examples/PadLab` and report.

### Constants

| Name | Value |
|---|---|
| `espidi::pin::PLAY` | GPIO1 |
| `espidi::pin::TAP` | GPIO2 (strapping) |
| `espidi::pin::PAGE` | GPIO3 (labelled L/R on the board) |
| `espidi::pin::ENC_SW` | GPIO4 |
| `espidi::pin::ENC_B` / `ENC_A` | GPIO5 / GPIO6 |
| `espidi::pin::MIDI_RX` / `MIDI_TX` | GPIO7 / GPIO10 — UART1, `espidi::MIDI_BAUD` = 31250, 8N1 |
| `espidi::pin::OLED_SDA` / `OLED_SCL` | GPIO8 / GPIO9 — I²C, `espidi::OLED_ADDR` = 0x3C, 128×32 |
| NVS | partition `nvs`, 16 KB shared by all firmwares |
| Files | partition `spiffs`, LittleFS, 1 MB shared |
| Max image | 2.62 MB alone; otherwise the free space `espidi_hub.py check`/`info` reports |

### Boot semantics

Power-on and the RESET button start the HUB. `esp_restart()`, panics, watchdogs and deep-sleep wake start your firmware again. `espidi::rebootToHub()` starts the HUB.

### Start from the template

`templates/arduino-firmware/` is a complete ESPidi-ready repository: `espidi.json`, `cover.png`, the published build in `espidi/`, the sketch (`ESPidiTemplate/` — display, buttons, MIDI out, settings, files, the way back to the HUB) and the workflow `.github/workflows/firmware.yml`.

### Verify before you report done

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c3 --warnings all \
  --library <ESPidi-HUB>/sdk/ESPidiHub --output-dir out <SketchFolder>
python3 <ESPidi-HUB>/tools/espidi_hub.py check --require-descriptor out/<Sketch>.ino.bin
python3 <ESPidi-HUB>/tools/espidi_hub.py repo <repo-folder> --build out/<Sketch>.ino.bin
```

Done means: it compiles without warnings from your code, `check` and `repo` exit 0 (`check` prints `cover: ESPIDI_COVER() inside` if there is a cover), and a search of your sources finds none of the forbidden calls from rule 5. If a sketch exceeds Arduino's 1.25 MB default, compile with `esp32:esp32:esp32c3:PartitionScheme=huge_app` (this lifts only Arduino's check).

If the user can test on hardware, ask them to confirm: both slots start, settings of the other firmware survive, power cycle → HUB, `rebootToHub()` → HUB.

---

## Task B — changing ESPidi HUB itself

### Map

| Path | What |
|---|---|
| `hub/bootloader_components/main/bootloader_start.c` | second-stage bootloader; ESP-IDF v5.5 `bootloader_start.c` + `espidi_hub_select()` |
| `hub/main/` | HUB app (pure ESP-IDF, C): `main.c` flow, `oled.c`, `font.c`, `input.c`, `slots.c` |
| `hub/partitions.csv` | empty flash layout |
| `tools/espidi_hub.py` | installer, layout math, partition table / metadata / image formats |
| `tools/tests/` | unit tests (`python3 -m unittest discover -s tools/tests`) |
| `sdk/ESPidiHub/` | Arduino library for firmwares |
| `web/` | composer site, https://espidi-hub.github.io/ (repository `espidi-hub/espidi-hub.github.io`; publish: `git push https://github.com/espidi-hub/espidi-hub.github.io.git $(git subtree split --prefix web):refs/heads/main`): `espidi.js` = port of `tools/espidi_hub.py`, `github.js` (ESPidi-ready repositories = `parse_spec`/`check_repo` in Python), `flasher.js` (esptool-js, vendored), `app.js` UI, `i18n.js` RU/EN, `pixels.js` (HUB-font text and the logo, both pages), `espidi.html` + `about.js` (the "What is ESPidi" page; `board.js` draws the board, its icon traced from a photo by `tools/trace_board_icon.py`), `catalog/` |
| `sdk/ESPidiHub/COVERS.md` | covers for firmware authors (EN, RU) |
| `templates/arduino-firmware/` | an ESPidi-ready firmware repository (the reference for `espidi.json`) |
| `sdk/ESPidiPad/` | Bluetooth SMC-PAD Pro library: `src/PadCore.h` (no BLE, host-tested), `src/ESPidiPad.cpp` (BLE), `src/SmcPadProtocol.h` = a copy of the kit's, `extras/smc_pad_ble/` = the kit, unchanged |
| `web/font.js` | generated by `tools/gen_web_font.py` from `hub/main/font.c` — never edit by hand |
| `docs/ARCHITECTURE.md` | the specification — update it with any change to formats or layout |

### Invariants — do not break

1. The flash layout is a contract with every installed device: offsets and sizes of `nvs`, `espidi`, `otadata`, `hub`, `spiffs`, and the slot anchors `0x50000` / `0x300000`. Changing them requires a format bump and a migration plan agreed with the user.
2. `hub/partitions.csv` == `empty_layout()` in `tools/espidi_hub.py`, byte for byte (CI checks; the unit test compares with `gen_esp32part.py` when it is available).
3. The partition table always contains `ota_0` and `ota_1` (empty slots keep a 64 KB placeholder).
4. Binary formats (metadata sector, cover blocks, firmware descriptor, firmware cover) and `espidi.json` (`check_manifest` / `validateManifest`, `parse_spec` / `parseSpec`) are defined in `hub/main/slots.c`, `tools/espidi_hub.py`, `sdk/ESPidiHub/src/ESPidiHub.h` and `web/espidi.js`. Change all of them and `docs/ARCHITECTURE.md` together. The start screen is drawn twice — `draw_start()` in `hub/main/main.c` and `drawStart()` in `web/app.js`; keep them identical.
5. The HUB and the installer never erase NVS or the file partition (only `base` erases the whole chip, after confirmation). The one exception: the HUB menu's FORGET PAD erases the `espidi_pad` namespace, after confirmation.
6. The HUB never auto-starts a slot after the bootloader refused it (no boot loops).
7. Keep the HUB under 256 KB and the bootloader under 32 KB (CI checks).
8. `web/espidi.js` must produce the same bytes as `tools/espidi_hub.py`. After changing either: `python3 tools/gen_web_vectors.py`, then open `web/test.html` over HTTP — all checks must pass. After changing the HUB font: `python3 tools/gen_web_font.py`. After adding a catalog firmware or cover: `python3 tools/update_catalog.py`. Catalog and template covers are drawn by `tools/make_covers.py`.
9. `sdk/ESPidiPad/src/SmcPadProtocol.h` stays byte-identical to `extras/smc_pad_ble/esp32/SmcPadProtocol.h`; the kit's files are not edited here (updates from its author replace the folder). After changing `PadCore.h` or the kit: `python3 sdk/ESPidiPad/extras/test/make_padcore_vectors.py`, then the unit tests.

### Build and test

- HUB + bootloader: GitHub Actions (`.github/workflows/build.yml`, `espressif/esp-idf-ci-action`, IDF v5.5, target `esp32c3`). Locally, if ESP-IDF is installed: `cd hub && idf.py set-target esp32c3 && idf.py build`.
- Installer and ESPidiPad (the kit's checks, `PadCore.h` on the host): `python3 -m unittest discover -s tools/tests -v`.
- SDK: compile the template (see Task A) and `sdk/ESPidiPad/examples/PadLab` with `--library sdk/ESPidiHub --library sdk/ESPidiPad`.
