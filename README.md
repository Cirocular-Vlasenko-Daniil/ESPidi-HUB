# ESPidi HUB

**A boot menu for the [ESPidi](https://github.com/EugeneCarlo/ESPidi) platform: two firmwares on one board, chosen at power-on.**

ESPidi HUB is a small bootloader + launcher for the ESP32-C3 Super Mini based ESPidi board. It keeps two firmwares — for example [RED SEA](https://github.com/Cirocular-Vlasenko-Daniil/Red-Sea) and another one — and starts one of them every time the board is powered on. Firmwares stay ordinary Arduino or ESP-IDF builds; the HUB takes care of where they live in flash.

Русская версия: [README.ru.md](README.ru.md).

## At power-on

| Hold | Starts |
|---|---|
| PLAY | slot 1 |
| L/R | slot 2 |
| nothing | the firmware used last time |
| encoder button | the HUB menu (installed firmwares, free space) |

Press during the short splash after the screen lights up. A restart from inside a firmware brings back the same firmware; power cycling or the RESET button brings back the HUB.

## Firmware size

There are no fixed slot sizes. Slot 1 grows up from one end of the firmware area and slot 2 grows down from the other, so the installer gives each firmware exactly the space it needs and checks the gap: up to **2.62 MB** for a firmware alone, or whatever is left next to the other one. Installing into one slot never moves or touches the other.

All firmwares share 16 KB of settings storage (NVS) and a 1 MB LittleFS partition for files, each in its own namespace and folder.

## Composer (web)

**[cirocular-vlasenko-daniil.github.io/ESPidi-HUB](https://cirocular-vlasenko-daniil.github.io/ESPidi-HUB/)** — pick a firmware for each slot from the catalog or load your own `.bin`, see how the flash is shared and what the HUB menu will show, then:

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
python3 tools/espidi_hub.py install -p /dev/cu.usbmodem1101 --slot 2 other.bin

# what is installed
python3 tools/espidi_hub.py info -p /dev/cu.usbmodem1101
```

`base` is the only command that erases settings. `image --hub-dir <dir> --slot1 a.bin --slot2 b.bin --out custom.bin` builds the same single image as the composer.

## Writing firmwares

- [docs/FIRMWARE_GUIDE.md](docs/FIRMWARE_GUIDE.md) — rules, pins, storage, size, build, publishing ([на русском](docs/FIRMWARE_GUIDE.ru.md)).
- [AGENTS.md](AGENTS.md) — the same rules as a checklist for AI coding agents.
- `sdk/ESPidiHub` — Arduino library: pin map, `ESPIDI_FIRMWARE()` descriptor, safe file storage, `rebootToHub()`.
- `templates/arduino-firmware` — a starting sketch and a GitHub Actions workflow that builds and checks the `.bin`.

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
