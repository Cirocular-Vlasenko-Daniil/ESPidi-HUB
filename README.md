# ESPidi HUB

**A boot menu for the [ESPidi](https://github.com/EugeneCarlo/ESPidi) platform: two firmwares on one board, chosen at power-on.**

ESPidi HUB is a small bootloader + launcher for the ESP32-C3 Super Mini based ESPidi board. It keeps two firmwares — for example [RED SEA](https://github.com/Cirocular-Vlasenko-Daniil/Red-Sea) and another one — and starts one of them every time the board is powered on. Firmwares stay ordinary Arduino or ESP-IDF builds; the HUB takes care of where they live in flash.

Русская версия: [README.ru.md](README.ru.md).

## At power-on

| Hold | Starts |
|---|---|
| PLAY | slot 1 |
| PAGE | slot 2 |
| nothing | the firmware used last time |
| encoder button | the HUB menu (installed firmwares, free space) |

Press during the short splash after the screen lights up. While a firmware starts, the HUB shows its **cover** — a 64×32 picture — with the name and version. A restart from inside a firmware brings back the same firmware; power cycling or the RESET button brings back the HUB.

## Firmware size

There are no fixed slot sizes. Slot 1 grows up from one end of the firmware area and slot 2 grows down from the other, so the installer gives each firmware exactly the space it needs and checks the gap: up to **2.62 MB** for a firmware alone, or whatever is left next to the other one. Installing into one slot never moves or touches the other.

All firmwares share 16 KB of settings storage (NVS) and a 1 MB LittleFS partition for files, each in its own namespace and folder.

## Composer (web)

**[espidi-hub.github.io](https://espidi-hub.github.io/)** — pick a firmware for each slot from the catalog, **paste a GitHub repository** made with the SDK (`owner/repo` — the firmware, its cover and description come straight from it) or load your own `.bin`, give it a cover (the firmware's own, or any picture — 64×32 and its multiples go pixel for pixel, anything else is scaled and dithered), see how the flash is shared and what the HUB will show, then:

- **write it to the ESPidi over USB** right from the browser (Chrome, Edge or Opera on a computer): a full install, or an update of the slots that keeps settings and files;
- or **download one image** for a fresh install with any flashing tool (`esptool --chip esp32c3 write_flash 0x0 espidi-custom.bin`).

The site's source is in `web/`; its logic is a port of the installer below and is checked against it byte for byte (`web/test.html`).

## Installing from the command line

Needs Python 3 and `pip install esptool`:

```bash
# once: erases the flash, writes the bootloader and the HUB (downloaded from the latest release)
python3 tools/espidi_hub.py base -p /dev/cu.usbmodem1101

# put firmwares into the slots
python3 tools/espidi_hub.py install -p /dev/cu.usbmodem1101 --slot 1 REDSEA.ino.bin
python3 tools/espidi_hub.py install -p /dev/cu.usbmodem1101 --slot 2 other.bin --cover other.png
python3 tools/espidi_hub.py install -p /dev/cu.usbmodem1101 --slot 2 owner/repo   # an ESPidi-ready repository

# what is installed
python3 tools/espidi_hub.py info -p /dev/cu.usbmodem1101
```

`install` takes the cover built into the firmware unless `--cover` (PNG/PBM, 64×32 or a multiple) or `--no-cover` is given. `base` is the only command that erases settings. `image --hub-dir <dir> --slot1 a.bin --slot2 b.bin --out custom.bin` builds the same single image as the composer.

## Writing firmwares

- [docs/FIRMWARE_GUIDE.md](docs/FIRMWARE_GUIDE.md) — rules, pins, storage, size, build, publishing ([на русском](docs/FIRMWARE_GUIDE.ru.md)).
- [AGENTS.md](AGENTS.md) — the same rules as a checklist for AI coding agents.
- `sdk/ESPidiHub` — Arduino library: pin map, `ESPIDI_FIRMWARE()` descriptor, `ESPIDI_COVER()`, safe file storage, `rebootToHub()`. Covers: [sdk/ESPidiHub/COVERS.md](sdk/ESPidiHub/COVERS.md).
- `sdk/ESPidiPad` — an M-VAVE SMC-PAD Pro over Bluetooth as a controller: events, RGB pads, shared pairing ([README](sdk/ESPidiPad/README.md)); built on Ruslan Mazavin's SMC-PAD Pro BLE kit.
- `templates/arduino-firmware` — an ESPidi-ready repository to copy: sketch, `espidi.json`, `cover.png` and a GitHub Actions workflow that builds, checks and publishes the `.bin` so the composer installs it from a link.

## Repository

| Path | What |
|---|---|
| `hub/` | ESP-IDF project: the HUB app and the custom bootloader |
| `tools/espidi_hub.py` | installer (slot sizing, partition table, metadata) |
| `web/` | the composer site: `espidi.js` (port of the installer), `app.js`, firmware catalog |
| `sdk/`, `templates/` | for firmware authors |
| `docs/ARCHITECTURE.md` | boot flow, flash layout, binary formats |

The HUB and the bootloader are built by GitHub Actions with ESP-IDF v5.5; tagged versions publish `bootloader.bin`, `espidi_hub.bin` and the installer as a release.

## Status

Early version, not yet tested on hardware. The splash screen is a placeholder.

## License

MIT, see [LICENSE](LICENSE). The bootloader is based on ESP-IDF's `bootloader_start.c` (Apache-2.0, Espressif Systems).

## Credits

- ESPidi HUB: Daniil Vlasenko
- ESPidi hardware platform: Eugene Carlo
