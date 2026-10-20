<!-- From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md. -->

# Open questions, and how each will be settled

Ruslan's bench will answer these on his own SMC-PAD Pro and send the results as an update of this kit. Each also
has a test any ESP32 host can run; if you run one, report the pad's identity reply (`SMC-PAD_00x`) with the result.

| # | question | why it matters | test |
|---|---|---|---|
| 1 | **Is the "session" only flow control?** spectalive needed MidiSuite's full connect (identity, region 4, all of region 5) before colour writes worked. The firmware has no unlock state. | if yes, connecting takes 2 round trips instead of 31 (no 28 KiB read) | subscribe to `AE42`, send `0x11`, wait for the reply, then write one pad colour: does the OK come and the pad change? Then the same without waiting for the identity reply. |
| 2 | Does the busy flag stick when nobody is subscribed to `AE42`? | a host that forgets the CCC would lock the pad's config path | write a colour without subscribing, then subscribe and retry |
| 3 | Pairing: does the pad require pairing or a bond? Public or random address? | the NimBLE central's security settings and the stored address | connect from the ESP32 without security; then with just-works pairing |
| 4 | The pad's ATT MTU and connection interval | write piece size, LED animation rate | MTU exchange from the ESP32; read `2A04`; time a colour write's round trip |
| 5 | Colours are lost at power-off without save0 | whether a host must repaint at connect | write a colour, power-cycle, read the record |
| 6 | Pad records: type field, velocity or fixed, bytes `[0]`, `[3]`, `[9..25]` | interpreting pad hits from the config | hit pads softly and hard; change `[0]` / `[3]` of one record in RAM and observe |
| 7 | Encoder records 8-15 (the pad has 8 knobs) | whether records 8-15 matter | change records 8-15 in RAM and turn every knob, with and without the bank button |
| 8 | Where MIDI written to the pad (BLE-MIDI characteristic, command `0x15`) goes: USB out? 3.5 mm out? | whether ESPidi can use the pad as a BLE-to-wired MIDI bridge | send a note both ways while monitoring the USB and 3.5 mm outputs |
| 9 | The region-4 globals other than byte 10 (active profile) | settings a host could show | read region 4, change each setting in MidiSuite, read again |
| 10 | The `AE00` service's handler | probably unused; confirm it is safe to ignore | none needed unless it turns out to be used |
