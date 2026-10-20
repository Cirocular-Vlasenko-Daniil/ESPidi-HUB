# Arduino firmware template

1. Copy this folder into a new repository.
2. Rename `ESPidiTemplate/` and `ESPidiTemplate.ino` to your sketch name, set `SKETCH` in `.github/workflows/firmware.yml`.
3. Change `ESPIDI_FIRMWARE("template", "TEMPLATE", "0.1.0", "Your Name")` — the id is also your NVS namespace and folder.
4. Install the SDK (`sdk/ESPidiHub` from ESPidi-HUB) into your Arduino `libraries` folder.

Rules: [docs/FIRMWARE_GUIDE.md](../../docs/FIRMWARE_GUIDE.md) · for agents: [AGENTS.md](../../AGENTS.md).
