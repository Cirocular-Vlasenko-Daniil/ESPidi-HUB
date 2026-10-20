# HUB binaries for the web composer

The composer looks for `manifest.json` here:

```json
{ "version": "0.1.0", "bootloader": "bootloader.bin", "hub": "espidi_hub.bin" }
```

and the two files it names — `hub/build/bootloader/bootloader.bin` and `hub/build/espidi_hub.bin`
from the HUB build. Without them the composer still composes and checks, but full install and the
image download stay off.
