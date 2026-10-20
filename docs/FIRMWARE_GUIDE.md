# Writing a firmware for ESPidi HUB

ESPidi HUB keeps two firmwares on one ESPidi board and starts one of them at power-on. This guide is everything a firmware needs to live in a slot. AI agents: the same rules in checklist form are in [AGENTS.md](../AGENTS.md). Русская версия: [FIRMWARE_GUIDE.ru.md](FIRMWARE_GUIDE.ru.md).

## What your firmware gets

- A **slot**: PLAY starts slot 1, PAGE starts slot 2, or the HUB starts the one used last time.
- **Settings**: the shared NVS (16 KB), in your own namespace.
- **Files**: the shared 1 MB LittleFS, in your own folder.
- **Space**: up to 2.62 MB of code when the other slot is empty; otherwise whatever is left next to it. You don't pick a size — the installer does.
- **The whole board** while it runs: display, buttons, encoder, MIDI, Wi-Fi/BLE.

A firmware that follows the rules below does not need to know about the HUB at all. RED SEA, for example, runs unmodified.

## Quick start (Arduino)

1. Install the ESP32 core (Espressif, 3.x) and select **ESP32C3 Dev Module**.
2. Install the SDK: copy `sdk/ESPidiHub` to your Arduino `libraries` folder (or pass `--library sdk/ESPidiHub` to arduino-cli).
3. Start from `templates/arduino-firmware/ESPidiTemplate`.
4. Put a descriptor in the sketch — the HUB shows its name:

```cpp
#include <ESPidiHub.h>
ESPIDI_FIRMWARE("myarp", "MY ARP", "1.0.0", "Your Name");
```

`id` (max 15 characters, lowercase) is also your NVS namespace and your folder name. `name` and `author` max 23 characters, `version` max 15. A longer string is a compile error. Reserved ids — the HUB and the SDK use these NVS namespaces: `espidihub`, `espidi_pad`, `nimble_bond`.

## Rules

**Must**

1. Target the ESP32-C3 and build an ordinary app image. The file to install is `<sketch>.ino.bin`, **not** `*.merged.bin` and not the bootloader or partition files.
2. Use the ESPidi pin map (`espidi::pin::…`).
3. Keep settings in NVS under **your own namespace** (`Preferences prefs; prefs.begin("<id>");`).
4. Keep files on the shared LittleFS in **`/<id>/`**, mounted with `espidi::mountFiles()`.
5. Fit the space the installer reports (`espidi_hub.py check`).

**Must not**

1. Erase or format shared storage: no `nvs_flash_erase()`, no `LittleFS.format()`, no `LittleFS.begin(true)`; `Preferences::clear()` only on your own namespace. Another firmware's settings and files live there too.
2. Use SPIFFS or FFat on the shared partition — only LittleFS. A different file system would make the partition unreadable for everyone.
3. Use OTA updates (`Update`, `esp_ota_begin()`, `esp_https_ota()`), call `esp_ota_set_boot_partition()` or write otadata — slot selection belongs to the HUB. To leave, call `espidi::rebootToHub()`.
4. Write raw flash or change the partition table.
5. Rely on the board booting with TAP held: GPIO2 is a strapping pin.

## Hardware

| Signal | GPIO | Notes |
|---|---|---|
| PLAY | 1 | button, `INPUT_PULLUP`, active LOW |
| TAP | 2 | button; strapping pin — must not be LOW at reset |
| PAGE | 3 | button, labelled L/R on the board |
| Encoder button | 4 | |
| Encoder B / A | 5 / 6 | `INPUT_PULLUP` |
| MIDI RX / TX | 7 / 10 | UART, 31250 baud, 8N1 |
| OLED SDA / SCL | 8 / 9 | SSD1306 128×32, I²C address `0x3C`; both strapping pins |

Constants: `espidi::pin::PLAY` … `espidi::pin::MIDI_TX`, `espidi::OLED_ADDR`, `espidi::OLED_WIDTH`, `espidi::OLED_HEIGHT`, `espidi::MIDI_BAUD`.

## Storage

**Settings** — NVS is small (16 KB for everyone). Store a handful of values, not banks of data:

```cpp
Preferences prefs;
prefs.begin("myarp");            // = descriptor id
uint8_t bpm = prefs.getUChar("bpm", 120);
```

**Files** — presets, patterns, MIDI files:

```cpp
if (espidi::mountFiles()) {
  String dir = espidi::dataDir("myarp");   // creates /myarp
  File f = LittleFS.open(dir + "/preset1.json", "w");
}
```

`mountFiles()` formats the partition only if it is completely blank (fresh install). If it fails to mount for any other reason it returns `false` and leaves the data alone — run without files in that case.

## Size

- The installer rounds your image up to 64 KB and checks it against the free space.
- Alone on the board: up to **2.62 MB**. Next to another firmware: 2.69 MB minus that firmware.
- The Arduino IDE limits sketches to 1.25 MB with the default partition scheme. If yours is bigger, choose **Tools → Partition Scheme → Huge APP**; this only lifts Arduino's check — the HUB layout is not affected.

## Boot behaviour

| Event | What starts |
|---|---|
| Power on, RESET button | the HUB |
| `ESP.restart()` / `esp_restart()` | your firmware again |
| Crash, watchdog | your firmware again |
| Deep-sleep wake | your firmware |
| `espidi::rebootToHub()` | the HUB |

Optional helpers: `espidi::underHub()` — started by the HUB; `espidi::slot()` — 1 or 2 (0 without the HUB).

A good place for "back to the HUB" is a long press (the template uses the encoder button for 2 s).

## Build, check, install

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c3 --library path/to/ESPidi-HUB/sdk/ESPidiHub --output-dir out MySketch
python3 tools/espidi_hub.py check --require-descriptor out/MySketch.ino.bin
python3 tools/espidi_hub.py install -p /dev/cu.usbmodem1101 --slot 2 out/MySketch.ino.bin
```

`check` reports the size, the slot space it takes, the descriptor and any problem (wrong chip, merged image, too big). `install` sizes the slot, writes the firmware and keeps the other slot, all settings and files intact.

## Publishing

Copy `templates/arduino-firmware/.github/workflows/firmware.yml` into your repository and set `SKETCH`. On every push it builds the `.bin` and runs `check`; on a `v*` tag it attaches the `.bin` to the release.

## ESP-IDF firmwares

The same rules apply. Without the Arduino SDK, add the descriptor by hand:

```c
__attribute__((used, retain, aligned(4))) const struct {
    char magic[12], id[16], name[24], version[16], author[24];
} espidi_firmware_descriptor = {"ESPIDI-FW-1", "myfw", "MY FW", "1.0.0", "Your Name"};
```

The shared file system is the data partition labelled `spiffs` (LittleFS); NVS is the default `nvs` partition.

## Before you release

- [ ] `check --require-descriptor` passes.
- [ ] Installed next to another firmware: both still start, both keep their settings.
- [ ] Power cycle → HUB; your restart → your firmware; `rebootToHub()` → HUB.
- [ ] Files (if used) live in `/<id>/` and survive a reinstall of the other slot.
- [ ] Nothing in the code formats, erases or does OTA.
