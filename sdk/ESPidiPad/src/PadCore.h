// ESPidiPad, the half without BLE: the connect sequence, one config request in flight with
// timeouts and resends, the pad's active profile, the LED colours still to send, and the pad's
// MIDI turned into control events. C++11, no heap, no BLE, no Arduino — tested on a computer by
// extras/test/test_padcore.cpp. The BLE half is ESPidiPad.cpp.
//
// Protocol and the rules followed here: extras/smc_pad_ble/PROTOCOL.md (the SMC-PAD Pro BLE kit
// by Ruslan Mazavin / pi32v2_re, see extras/smc_pad_ble/NOTICE.md). Packets are built only by
// its SmcPadProtocol.h, which refuses flash writes, calibration writes and out-of-range access.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "SmcPadProtocol.h"

namespace espidi {

// Something the pad did, delivered in loop() by Pad::read().
struct PadEvent {
  enum Kind : uint8_t {
    NONE,
    PAD,           // index = pad record 0-127 (bank() * 16 + pad()); value = velocity, 0 = released
    ENCODER,       // index = encoder record 0-15; value = what it sent; delta = change (relative: ±1)
    BUTTON,        // index = button record 0-4; value = 1 pressed, 0 released
    MIDI,          // a message the pad's profile doesn't explain: see midi[]
    CONNECTED,     // a pad is connected; its profile is being read
    READY,         // the profile is read: events are mapped and LEDs can be set
    DISCONNECTED,
    PAIRED,        // pair() found a pad and remembered it
    PAIR_FAILED,   // pair() found no pad in time
  };
  Kind kind;
  uint8_t index;
  uint8_t value;
  int8_t delta;
  uint8_t len;        // bytes in midi[]
  uint8_t midi[16];   // the MIDI message behind the event (a longer SysEx is cut)

  PadEvent() : kind(NONE), index(0), value(0), delta(0), len(0) { memset(midi, 0, sizeof(midi)); }
  uint8_t bank() const { return index / 16; }
  uint8_t pad() const { return index % 16; }
};

namespace padcore {

static const uint8_t BUTTONS = 5, ENCODERS = 16, PADS = 128, BANKS = 8, BANK_PADS = 16;

// The active profile (region 5 records, PROTOCOL.md 2.2) and what its controls send.
class Mapper {
 public:
  uint8_t rec[smcpad::PROFILE_SIZE];   // the profile as the pad holds it
  bool valid;

  Mapper() : valid(false) { memset(rec, 0, sizeof(rec)); forget(); }
  void forget() { for (uint8_t e = 0; e < ENCODERS; e++) last_[e] = -1; }

  const uint8_t* button(uint8_t b) const { return rec + smcpad::BUTTON_OFF + b * smcpad::BUTTON_SIZE; }
  const uint8_t* encoder(uint8_t e) const { return rec + smcpad::ENCODER_OFF + e * smcpad::ENCODER_SIZE; }
  const uint8_t* pad(uint8_t i) const { return rec + smcpad::PAD_OFF + i * smcpad::PAD_SIZE; }

  // One MIDI message from the pad -> an event. Without a profile everything is MIDI.
  void map(const uint8_t* m, size_t n, PadEvent& e) {
    e = PadEvent();
    e.kind = PadEvent::MIDI;
    e.len = (uint8_t)(n < sizeof(e.midi) ? n : sizeof(e.midi));
    memcpy(e.midi, m, e.len);
    if (!valid || n == 0) return;
    const uint8_t st = m[0], hi = st & 0xF0, ch = st & 0x0F;

    if (st == 0xF0) {                                        // type 4: a raw message, e.g. MMC
      for (uint8_t b = 0; b < BUTTONS; b++) {
        const uint8_t* r = button(b);
        if (r[0] == 4 && r[5] == n && n <= smcpad::BUTTON_SIZE - 6 && memcmp(r + 6, m, n) == 0)
          return hit(e, PadEvent::BUTTON, b, 1, 0);
      }
      return;
    }
    if ((hi == 0x90 || hi == 0x80) && n >= 3) {
      const uint8_t vel = hi == 0x80 ? 0 : m[2];
      for (uint8_t b = 0; b < BUTTONS; b++) {               // type 5: note, always channel 1
        const uint8_t* r = button(b);
        if (r[0] == 5 && ch == 0 && r[2] == m[1]) return hit(e, PadEvent::BUTTON, b, vel ? 1 : 0, 0);
      }
      for (uint8_t i = 0; i < PADS; i++) {                  // pads: [1] channel, [2] note
        const uint8_t* r = pad(i);
        if ((r[1] & 0x0F) == ch && r[2] == m[1]) return hit(e, PadEvent::PAD, i, vel, 0);
      }
      return;
    }
    if (hi == 0xB0 && n >= 3) {
      for (uint8_t b = 0; b < BUTTONS; b++) {
        const uint8_t* r = button(b);
        if ((r[1] & 0x0F) != ch || r[2] != m[1]) continue;
        if (r[0] == 0 || r[0] == 1) return hit(e, PadEvent::BUTTON, b, 1, 0);       // trigger, toggle
        if (r[0] == 2) return hit(e, PadEvent::BUTTON, b, m[2] == r[3] && r[3] != r[4] ? 0 : 1, 0);  // momentary
      }
      for (uint8_t k = 0; k < ENCODERS; k++) {
        const uint8_t* r = encoder(k);
        if ((r[2] & 0x0F) != ch || r[3] != m[1]) continue;
        if (r[0] == 0) return absolute(e, k, m[2]);
        if (r[0] == 3) return hit(e, PadEvent::ENCODER, k, m[2], m[2] == r[5] ? 1 : m[2] == r[4] ? -1 : 0);
      }
      return;
    }
    if (hi == 0xD0 && n >= 2) {                              // type 1: channel pressure
      for (uint8_t k = 0; k < ENCODERS; k++)
        if (encoder(k)[0] == 1 && (encoder(k)[2] & 0x0F) == ch) return absolute(e, k, m[1]);
      return;
    }
    if (hi == 0xE0 && n >= 3) {                              // type 2: pitch bend, value in the MSB
      for (uint8_t k = 0; k < ENCODERS; k++)
        if (encoder(k)[0] == 2 && (encoder(k)[2] & 0x0F) == ch) return absolute(e, k, m[2]);
      return;
    }
    if (hi == 0xC0 && n >= 2) {                              // type 3: program change (after its bank CCs)
      for (uint8_t b = 0; b < BUTTONS; b++) {
        const uint8_t* r = button(b);
        if (r[0] == 3 && (r[1] & 0x0F) == ch && r[4] == m[1]) return hit(e, PadEvent::BUTTON, b, 1, 0);
      }
    }
  }

 private:
  int16_t last_[ENCODERS];

  static void hit(PadEvent& e, PadEvent::Kind k, uint8_t index, uint8_t value, int delta) {
    e.kind = k;
    e.index = index;
    e.value = value;
    e.delta = (int8_t)(delta < -128 ? -128 : delta > 127 ? 127 : delta);
  }
  void absolute(PadEvent& e, uint8_t k, uint8_t v) {
    hit(e, PadEvent::ENCODER, k, v, last_[k] < 0 ? 0 : (int)v - last_[k]);
    last_[k] = v;
  }
};

// The config conversation with one connected pad (PROTOCOL.md sections 3 and 5): poll() says
// what to send, reply() takes the pad's answers. One request in flight; a missing reply is
// normal (busy, ignored) and is resent; TRIES missing replies in a row mean the link is bad.
class Session {
 public:
  enum Stage : uint8_t { OFF, IDENTITY, GLOBALS, PROFILE, READY };
  static const uint32_t TIMEOUT_MS = 800;         // identity, region 4, colours
  static const uint32_t READ_TIMEOUT_MS = 4000;   // 1,009 bytes of region 5 at the smallest MTU
  static const uint8_t TRIES = 3;
  static const uint32_t POLL_MS = 2000;           // region 4: the active profile may change on the pad
  static const uint8_t BANK_WRITE_FROM = 4;       // this many changed pads in a bank -> one bank write

  Mapper map;
  uint32_t lastRtt, maxRtt, requests, resends, writeErrors;

  Session() { stop(); clearColors(); }

  // fullSync: read all of region 5 at connect, as MidiSuite does (31 round trips); the kit asks
  // for that until bench question 1 says it is not needed. Otherwise only the active profile.
  void start(bool fullSync, uint32_t now) {
    stop();
    full_ = fullSync;
    stage_ = IDENTITY;
    total_ = 2 + chunks(full_ ? 0 : 1);
    lastPoll_ = now;
  }
  void stop() {
    stage_ = OFF;
    req_.kind = NONE;
    failed_ = false;
    done_ = total_ = 0;
    ident_[0] = 0;
    memset(globals_, 0, sizeof(globals_));
    profile_ = 0;
    map.valid = false;
    map.forget();
    lastRtt = maxRtt = requests = resends = writeErrors = 0;
  }

  Stage stage() const { return stage_; }
  bool ready() const { return stage_ == READY; }
  bool failed() const { return failed_; }
  bool busy() const { return req_.kind != NONE; }
  uint8_t progress() const { return stage_ == READY ? 100 : total_ ? (uint8_t)(done_ * 100 / total_) : 0; }
  const char* identity() const { return ident_; }
  uint8_t profile() const { return profile_; }
  const uint8_t* globals() const { return globals_; }

  // The packet to send now (its length), or 0. Call it whenever there is time.
  int poll(uint32_t now, uint8_t* out, size_t cap) {
    if (stage_ == OFF || failed_) return 0;
    if (req_.kind != NONE) {
      uint32_t limit = TIMEOUT_MS;
      if (req_.kind == READ5) limit = READ_TIMEOUT_MS;
      if (now - req_.sentAt < limit) return 0;
      if (req_.tries >= TRIES) {
        failed_ = true;
        return 0;
      }
      req_.tries++;
      req_.sentAt = now;
      resends++;
      return build(out, cap);
    }
    if (!choose(now)) return 0;
    req_.tries = 1;
    req_.sentAt = now;
    requests++;
    int n = build(out, cap);
    if (n < 0) req_.kind = NONE;      // refused by the protocol rules: never sent
    return n > 0 ? n : 0;
  }

  // A complete packet from the pad (AE42). Anything that doesn't answer the request is ignored.
  void reply(const smcpad::Packet& p, uint32_t now) {
    if (req_.kind == NONE) return;
    switch (req_.kind) {
      case IDENT: {
        if (p.cmd != smcpad::IDENTITY) return;
        size_t n = p.len < sizeof(ident_) - 1 ? p.len : sizeof(ident_) - 1;
        memcpy(ident_, p.data, n);
        ident_[n] = 0;
        stage_ = GLOBALS;
        break;
      }
      case READ4:
      case POLL4: {
        const uint8_t* d = smcpad::read_reply(p, 4, 0, smcpad::REGION4_SIZE);
        if (!d) return;
        memcpy(globals_, d, sizeof(globals_));
        uint8_t prof = globals_[10] <= 7 ? globals_[10] : 0;
        if (req_.kind == READ4) {
          profile_ = prof;
          syncAddr_ = full_ ? 0 : profile_ * smcpad::PROFILE_SIZE;
          syncEnd_ = full_ ? smcpad::REGION5_SIZE : syncAddr_ + smcpad::PROFILE_SIZE;
          covered_ = 0;
          stage_ = PROFILE;
        } else if (prof != profile_) {           // switched on the pad: read the new profile
          profile_ = prof;
          syncAddr_ = profile_ * smcpad::PROFILE_SIZE;
          syncEnd_ = syncAddr_ + smcpad::PROFILE_SIZE;
          covered_ = 0;
          map.valid = false;
          map.forget();
          done_ = 0;
          total_ = chunks(1);
          stage_ = PROFILE;
        }
        break;
      }
      case READ5: {
        const uint8_t* d = smcpad::read_reply(p, 5, req_.addr, req_.len);
        if (!d) return;
        uint32_t base = profile_ * smcpad::PROFILE_SIZE;
        uint32_t a0 = req_.addr > base ? req_.addr : base;
        uint32_t a1 = req_.addr + req_.len < base + smcpad::PROFILE_SIZE ? req_.addr + req_.len : base + smcpad::PROFILE_SIZE;
        if (a0 < a1) {
          memcpy(map.rec + (a0 - base), d + (a0 - req_.addr), a1 - a0);
          covered_ += a1 - a0;
        }
        syncAddr_ += req_.len;
        if (syncAddr_ >= syncEnd_) {
          map.valid = covered_ == smcpad::PROFILE_SIZE;
          stage_ = READY;
          lastPoll_ = now;
          for (uint8_t i = 0; i < PADS; i++) {   // send the colours the host set that the pad doesn't show
            if (!isSet(set_, i)) memcpy(want_[i], map.pad(i) + smcpad::PAD_RGB, 3);
            mark(dirty_, i, isSet(set_, i) && memcmp(want_[i], map.pad(i) + smcpad::PAD_RGB, 3) != 0);
          }
        }
        break;
      }
      case COLOR:
      case BANK: {
        if (p.cmd != smcpad::OK) return;
        if (!p.ok()) {                           // the pad refused it: don't insist
          writeErrors++;
          break;
        }
        uint8_t first = req_.kind == COLOR ? req_.index : req_.index * BANK_PADS;
        uint8_t count = req_.kind == COLOR ? 1 : BANK_PADS;
        for (uint8_t i = first; i < first + count; i++)
          memcpy(rec_rgb(i), req_.kind == COLOR ? sent_ : bank_ + (i - first) * smcpad::PAD_SIZE + smcpad::PAD_RGB, 3);
        break;
      }
      default:
        return;
    }
    Kind answered = req_.kind;
    req_.kind = NONE;
    lastRtt = now - req_.sentAt;
    if (lastRtt > maxRtt) maxRtt = lastRtt;
    if ((answered == IDENT || answered == READ4 || answered == READ5) && done_ < total_) done_++;
  }

  // ---- LED colours (RAM only on the pad: no flash wear). Sent when READY, latest colour wins.
  bool setColor(uint8_t pad, uint8_t r, uint8_t g, uint8_t b) {
    if (pad >= PADS) return false;
    want_[pad][0] = r;
    want_[pad][1] = g;
    want_[pad][2] = b;
    mark(set_, pad, true);
    mark(dirty_, pad, !ready() || memcmp(want_[pad], map.pad(pad) + smcpad::PAD_RGB, 3) != 0 || busyWith(pad));
    return true;
  }
  bool getColor(uint8_t pad, uint8_t rgb[3]) const {
    if (pad >= PADS) return false;
    memcpy(rgb, want_[pad], 3);
    return true;
  }
  void clearColors() {
    memset(want_, 0, sizeof(want_));
    memset(set_, 0, sizeof(set_));
    memset(dirty_, 0, sizeof(dirty_));
    rr_ = 0;
  }
  uint8_t pending() const {
    uint8_t n = 0;
    for (uint8_t i = 0; i < PADS; i++) n += isSet(dirty_, i);
    return n;
  }

 private:
  enum Kind : uint8_t { NONE, IDENT, READ4, READ5, COLOR, BANK, POLL4 };
  struct Req {
    Kind kind;
    uint32_t addr, len;
    uint8_t index, tries;
    uint32_t sentAt;
  };
  Stage stage_;
  bool full_, failed_;
  Req req_;
  char ident_[28];
  uint8_t globals_[smcpad::REGION4_SIZE];
  uint8_t profile_;
  uint32_t syncAddr_, syncEnd_, covered_, lastPoll_;
  uint16_t done_, total_;
  uint8_t want_[PADS][3], set_[PADS / 8], dirty_[PADS / 8], rr_;
  uint8_t sent_[3], bank_[BANK_PADS * smcpad::PAD_SIZE];

  static bool isSet(const uint8_t* bits, uint8_t i) { return bits[i / 8] & (1 << (i % 8)); }
  static void mark(uint8_t* bits, uint8_t i, bool on) {
    if (on) bits[i / 8] |= (uint8_t)(1 << (i % 8));
    else bits[i / 8] &= (uint8_t)~(1 << (i % 8));
  }
  static uint16_t chunks(uint8_t profiles) {
    uint32_t bytes = profiles ? smcpad::PROFILE_SIZE : smcpad::REGION5_SIZE;
    return (uint16_t)((bytes + smcpad::READ_CHUNK - 1) / smcpad::READ_CHUNK);
  }
  uint8_t* rec_rgb(uint8_t i) { return map.rec + smcpad::PAD_OFF + i * smcpad::PAD_SIZE + smcpad::PAD_RGB; }
  bool busyWith(uint8_t pad) const {
    return (req_.kind == COLOR && req_.index == pad) || (req_.kind == BANK && req_.index == pad / BANK_PADS);
  }

  bool choose(uint32_t now) {
    switch (stage_) {
      case IDENTITY: req_.kind = IDENT; return true;
      case GLOBALS: req_.kind = READ4; return true;
      case PROFILE: {
        uint32_t left = syncEnd_ - syncAddr_;
        req_.kind = READ5;
        req_.addr = syncAddr_;
        req_.len = left < smcpad::READ_CHUNK ? left : smcpad::READ_CHUNK;
        return true;
      }
      case READY: {
        for (uint8_t b = 0; map.valid && b < BANKS; b++) {   // many changes in one bank: one write for all 16
          uint8_t n = 0;
          for (uint8_t i = b * BANK_PADS; i < (b + 1) * BANK_PADS; i++) n += isSet(dirty_, i);
          if (n >= BANK_WRITE_FROM) {
            req_.kind = BANK;
            req_.index = b;
            for (uint8_t i = b * BANK_PADS; i < (b + 1) * BANK_PADS; i++) mark(dirty_, i, false);
            return true;
          }
        }
        for (uint8_t k = 0; k < PADS; k++) {          // otherwise one pad, round robin
          uint8_t i = (uint8_t)((rr_ + k) % PADS);
          if (!isSet(dirty_, i)) continue;
          req_.kind = COLOR;
          req_.index = i;
          mark(dirty_, i, false);
          rr_ = (uint8_t)((i + 1) % PADS);
          return true;
        }
        if (now - lastPoll_ >= POLL_MS) {
          lastPoll_ = now;
          req_.kind = POLL4;
          return true;
        }
        return false;
      }
      default:
        return false;
    }
  }

  // Packets come only from SmcPadProtocol.h; its guards refuse anything the kit forbids.
  int build(uint8_t* out, size_t cap) {
    switch (req_.kind) {
      case IDENT: return smcpad::identity(out, cap);
      case READ4:
      case POLL4: return smcpad::read(4, 0, smcpad::REGION4_SIZE, out, cap);
      case READ5: return smcpad::read(5, req_.addr, req_.len, out, cap);
      case COLOR:
        memcpy(sent_, want_[req_.index], 3);
        return smcpad::color_write(profile_, req_.index, sent_[0], sent_[1], sent_[2], out, cap);
      case BANK: {
        uint8_t first = req_.index * BANK_PADS;      // whole records as read from the pad, new colours
        memcpy(bank_, map.pad(first), sizeof(bank_));
        for (uint8_t k = 0; k < BANK_PADS; k++) memcpy(bank_ + k * smcpad::PAD_SIZE + smcpad::PAD_RGB, want_[first + k], 3);
        return smcpad::write(5, smcpad::pad_addr(profile_, first), bank_, sizeof(bank_), out, cap);
      }
      default:
        return 0;
    }
  }
};

}  // namespace padcore
}  // namespace espidi
