// ESPidiPad — an M-VAVE SMC-PAD Pro over Bluetooth LE as a controller for an ESPidi firmware:
// its pads, encoders and buttons arrive as events in loop(), and its 128 RGB pad LEDs can be set.
// The pad's own firmware is not changed. Needs Arduino-ESP32 3.3+ (its BLE library on NimBLE).
//
//   #include <ESPidiPad.h>
//   espidi::Pad pad;
//
//   void setup() { pad.begin(); }            // reconnects to the remembered pad by itself
//   void loop() {
//     espidi::PadEvent e;
//     while (pad.read(e)) {
//       if (e.kind == espidi::PadEvent::PAD && e.value) pad.setColor(e.index, 255, 0, 0);
//     }
//   }
//
// Pairing: call pair() while the pad is in Bluetooth pairing mode. The pad is then remembered
// for every firmware on the board (NVS namespace "espidi_pad"); forget() clears it.
// Guide: sdk/ESPidiPad/README.md. Protocol and its rules: extras/smc_pad_ble/ — the SMC-PAD Pro
// BLE kit by Ruslan Mazavin (pi32v2_re) with facts from spectalive/smc-pad, see its NOTICE.md.
#pragma once
#include <Arduino.h>

#include "PadCore.h"

namespace espidi {

struct PadOptions {
  // Read all 28 KiB of the pad's profiles at connect (31 round trips), as MidiSuite does. The kit
  // asks for this until its bench answers whether reading only the active profile is enough.
  bool fullSync = true;
  // Pair with just-works bonding the first time, so the pad can find this board again.
  bool bond = true;
  // How long pair() looks for a pad in pairing mode.
  uint32_t pairTimeoutMs = 30000;
};

struct PadStats {
  uint32_t requests, resends, writeErrors;   // config requests sent, sent again, refused by the pad
  uint32_t lastRoundTripMs, maxRoundTripMs;  // config request -> reply
  uint32_t droppedEvents;                    // events lost because loop() didn't read() in time
  uint16_t mtu;                              // ATT MTU of the connection
  uint16_t intervalUs;                       // connection interval, 0 if unknown
  uint8_t addressType;                       // 0 public, 1 random
  bool encrypted, bonded;
};

class Pad {
 public:
  enum State : uint8_t {
    OFF,          // begin() not called
    NO_PAD,       // no pad remembered: call pair()
    PAIRING,      // looking for a pad in pairing mode
    CONNECTING,   // trying the remembered pad
    SYNCING,      // connected, reading its profile (progress())
    READY,        // events are mapped, LEDs can be set
  };

  // Starts Bluetooth and a background task. Call once, in setup().
  bool begin(const PadOptions& options = PadOptions());

  // The next event, if any. Call in loop() until it returns false.
  bool read(PadEvent& e);

  void pair();     // look for a pad in pairing mode for pairTimeoutMs -> PAIRED or PAIR_FAILED
  void forget();   // disconnect and forget the remembered pad (and its bond)

  State state() const;
  bool ready() const { return state() == READY; }
  bool remembered() const;
  uint8_t progress() const;       // 0-100 while SYNCING
  String identity() const;        // the pad's reply to the identity request, e.g. "SMC-PAD_004"
  String address() const;         // the remembered pad, "aa:bb:cc:dd:ee:ff"
  uint8_t profile() const;        // the pad's active user profile, 0-7
  PadStats stats() const;

  // LEDs. pad = record 0-127 = bank * 16 + pad in the bank. The colour lives in the pad's RAM:
  // it shows at once, wears no flash and is gone when the pad is switched off. Calls only
  // record the wish; the background task sends one request at a time, latest colour first, and
  // a whole bank at once when many of its pads changed.
  bool setColor(uint8_t pad, uint8_t r, uint8_t g, uint8_t b);
  void fillBank(uint8_t bank, uint8_t r, uint8_t g, uint8_t b);
  bool getColor(uint8_t pad, uint8_t rgb[3]) const;
  static uint8_t padIndex(uint8_t bank, uint8_t pad) { return (uint8_t)(bank * 16 + pad); }

  // What a control sends: a copy of its record in the pad's active profile (PROTOCOL.md 2.2).
  // False until the profile is read. Buffers: 26 bytes (pad), 6 (encoder), 23 (button).
  bool padRecord(uint8_t pad, uint8_t out[26]) const;
  bool encoderRecord(uint8_t encoder, uint8_t out[6]) const;
  bool buttonRecord(uint8_t button, uint8_t out[23]) const;

  // Log connection details (address type, MTU, security, timings) to out, e.g. &Serial — for
  // bench tests. May be called before begin(); nullptr stops it.
  void debug(Print* out);
};

}  // namespace espidi
