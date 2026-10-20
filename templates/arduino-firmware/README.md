# Arduino firmware template

1. Copy this folder into a new repository.
2. Rename `ESPidiTemplate/` and `ESPidiTemplate.ino` to your sketch name, set `SKETCH` in `.github/workflows/firmware.yml`.
3. Change `ESPIDI_FIRMWARE("template", "TEMPLATE", "0.1.0", "Your Name")` — the id is also your NVS namespace and folder.
4. Install the SDK (`sdk/ESPidiHub` from ESPidi-HUB) into your Arduino `libraries` folder.
5. Replace the cover: draw 64×32 (or 128×64, …) black and white, white = lit, then `python3 tools/espidi_hub.py cover my-cover.png > ESPidiTemplate/cover.h`. Delete `cover.h` and its `#include` for no cover.

Rules: [docs/FIRMWARE_GUIDE.md](../../docs/FIRMWARE_GUIDE.md) · for agents: [AGENTS.md](../../AGENTS.md).
