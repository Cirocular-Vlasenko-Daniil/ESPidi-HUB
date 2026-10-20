// Host test of PadCore.h against a fake pad that answers like the kit says the real one does,
// and against bytes from the kit's Python reference (make_padcore_vectors.py).
//   python3 make_padcore_vectors.py
//   c++ -std=c++11 -Wall -Wextra -I../../src test_padcore.cpp -o test_padcore && ./test_padcore
#include <stdio.h>
#include <string.h>

#include <initializer_list>

#include "PadCore.h"
#include "padcore_vectors.h"

using namespace espidi;
using padcore::Session;

static int fails = 0;
#define CHECK(cond, what)                                   \
  do {                                                      \
    if (cond) printf("ok   %s\n", what);                    \
    else { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } \
  } while (0)

// The pad's side of the config protocol (PROTOCOL.md 3.2-3.5), with a switch to stay silent.
struct FakePad {
  uint8_t region4[12];
  uint8_t region5[smcpad::REGION5_SIZE];
  int silent;                 // this many requests get no reply (busy pad)
  uint8_t out[2 * smcpad::MAX_PACKET];
  size_t outLen;

  FakePad() : silent(0), outLen(0) {
    static const uint8_t g[12] = {0x78, 0x00, 0x32, 0x04, 0x00, 0x00, 0x01, 0x01, 0x03, 0x01, FAKE_PROFILE, 0x00};
    memcpy(region4, g, 12);
    for (int p = 0; p < 8; p++) {
      memcpy(region5 + p * smcpad::PROFILE_SIZE, DEFAULT_PROFILE, smcpad::PROFILE_SIZE);
      region5[p * smcpad::PROFILE_SIZE + smcpad::PAD_OFF + 9] = (uint8_t)p;
    }
  }

  // One request in; the reply bytes go to out[].
  void handle(const uint8_t* pkt, size_t n) {
    outLen = 0;
    if (silent > 0) { silent--; return; }
    size_t len = pkt[3] | pkt[4] << 8 | pkt[5] << 16;
    if (n != len + 7 || pkt[n - 1] != smcpad::checksum(pkt + 6, len)) return;   // dropped, no reply
    const uint8_t* d = pkt + 6;
    if (pkt[2] == smcpad::IDENTITY) {
      uint8_t id[27] = {0};
      memcpy(id, "SMC-PAD_004", 11);
      outLen = smcpad::packet(smcpad::IDENTITY, id, sizeof id, out, sizeof out);
      return;
    }
    uint8_t region = d[0];
    uint32_t addr = d[1] | d[2] << 8 | d[3] << 16 | (uint32_t)d[4] << 24, n2 = d[5] | d[6] << 8 | d[7] << 16;
    uint8_t* mem = region == 4 ? region4 : region == 5 ? region5 : nullptr;
    if (!mem) return;
    if (pkt[2] == smcpad::READ) {
      uint8_t data[8 + smcpad::READ_CHUNK];
      memcpy(data, d, 8);
      memcpy(data + 8, mem + addr, n2);
      outLen = smcpad::packet(smcpad::READ, data, 8 + n2, out, sizeof out);
    } else if (pkt[2] == smcpad::WRITE) {
      memcpy(mem + addr, d + 8, n2);
      const uint8_t ok = 0;
      outLen = smcpad::packet(smcpad::OK, &ok, 1, out, sizeof out);
    }
  }
};

// Runs the session against the pad, the reply cut into 20-byte notifications (smallest MTU).
struct Link {
  Session s;
  FakePad pad;
  smcpad::Reassembler rx;
  uint32_t now = 1000;
  uint8_t tx[smcpad::MAX_PACKET];
  uint8_t sent[64][smcpad::MAX_PACKET];
  size_t sentLen[64];
  int sentCount = 0;

  // Advances time by 10 ms steps until `until` ms have passed or the session is idle and ready.
  void run(uint32_t ms, bool stopWhenIdle = true) {
    for (uint32_t t = 0; t < ms; t += 10, now += 10) {
      int n = s.poll(now, tx, sizeof tx);
      if (n > 0) {
        if (sentCount < 64) { memcpy(sent[sentCount], tx, n); sentLen[sentCount] = n; }
        sentCount++;
        pad.handle(tx, n);
        for (size_t k = 0; k < pad.outLen; k += 20) {
          rx.feed(pad.out + k, pad.outLen - k < 20 ? pad.outLen - k : 20);
          smcpad::Packet p;
          while (rx.next(p)) s.reply(p, now);
        }
      } else if (stopWhenIdle && s.ready() && !s.busy() && !s.pending()) {
        return;
      }
    }
  }
  bool sentEquals(int from, const uint8_t* bytes, const uint16_t* lens, size_t count) {
    if (sentCount - from != (int)count) return false;
    size_t off = 0;
    for (size_t i = 0; i < count; i++) {
      if (sentLen[from + i] != lens[i] || memcmp(sent[from + i], bytes + off, lens[i]) != 0) return false;
      off += lens[i];
    }
    return true;
  }
};

static PadEvent ev(Session& s, std::initializer_list<uint8_t> m) {
  uint8_t b[16];
  size_t n = 0;
  for (uint8_t x : m) b[n++] = x;
  PadEvent e;
  s.map.map(b, n, e);
  return e;
}

int main() {
  {
    static Link l;
    l.s.start(true, l.now);
    l.run(20000);
    CHECK(l.sentEquals(0, FULL_SEQUENCE, FULL_SEQUENCE_LEN, FULL_SEQUENCE_COUNT),
          "full sync sends MidiSuite's 31 requests byte for byte (the captured session)");
    CHECK(l.s.ready() && strcmp(l.s.identity(), "SMC-PAD_004") == 0 && l.s.profile() == FAKE_PROFILE,
          "identity and active profile");
    CHECK(l.s.map.valid && l.s.map.pad(0)[9] == FAKE_PROFILE &&
              memcmp(l.s.map.rec + 9, DEFAULT_PROFILE + 9, smcpad::PAD_OFF) == 0,
          "keeps the active profile's records");
    CHECK(l.s.progress() == 100 && l.s.resends == 0, "progress 100, no resends");
  }
  {
    static Link l;
    l.s.start(false, l.now);
    l.run(20000);
    CHECK(l.sentEquals(0, QUICK_SEQUENCE, QUICK_SEQUENCE_LEN, QUICK_SEQUENCE_COUNT),
          "quick sync: identity, region 4, the active profile only");

    // LEDs: one pad -> one colour write; the same colour again -> nothing
    int before = l.sentCount;
    l.s.setColor(3, 255, 0, 0);
    l.run(200);
    CHECK(l.sentCount == before + 1 && l.sentLen[before] == sizeof COLOR_PAD3_RED &&
              memcmp(l.sent[before], COLOR_PAD3_RED, sizeof COLOR_PAD3_RED) == 0,
          "setColor -> color_write bytes of the reference");
    const uint8_t* rgb = l.pad.region5 + FAKE_PROFILE * smcpad::PROFILE_SIZE + smcpad::PAD_OFF + 3 * smcpad::PAD_SIZE + 5;
    CHECK(rgb[0] == 255 && rgb[1] == 0 && rgb[2] == 0, "the pad has the colour");
    before = l.sentCount;
    l.s.setColor(3, 255, 0, 0);
    l.run(200);
    CHECK(l.sentCount == before, "the same colour is not sent again");

    // a whole bank changed -> one bank write of whole records
    before = l.sentCount;
    for (uint8_t k = 0; k < 16; k++) l.s.setColor(16 + k, k * 10, 255 - k, k);
    l.run(500);
    CHECK(l.sentCount == before + 1 && l.sentLen[before] == sizeof BANK1_WRITE &&
              memcmp(l.sent[before], BANK1_WRITE, sizeof BANK1_WRITE) == 0,
          "16 colours in a bank -> one write of the 16 records (bank_colors of the reference)");

    // the active profile changes on the pad -> the next region 4 poll notices and reads it
    l.pad.region4[10] = 3;
    before = l.sentCount;
    l.run(Session::POLL_MS + 500, false);
    int afterPoll = l.sentCount;
    l.sentCount = before + 1 + (int)QUICK_SEQUENCE_P3_COUNT;     // compare the poll and the reads only
    CHECK(l.sentLen[before] == QUICK_SEQUENCE_LEN[1] &&
              memcmp(l.sent[before], QUICK_SEQUENCE + QUICK_SEQUENCE_LEN[0], QUICK_SEQUENCE_LEN[1]) == 0 &&
              l.sentEquals(before + 1, QUICK_SEQUENCE_P3, QUICK_SEQUENCE_P3_LEN, QUICK_SEQUENCE_P3_COUNT),
          "profile switched on the pad -> a region 4 poll, then profile 3 is read");
    l.sentCount = afterPoll;
    const uint8_t* p3 = l.pad.region5 + 3 * smcpad::PROFILE_SIZE + smcpad::PAD_OFF;
    CHECK(p3[3 * smcpad::PAD_SIZE + 5] == 255 && p3[(16 + 15) * smcpad::PAD_SIZE + 5] == 150,
          "the colours the firmware set are put on the new profile too");
    CHECK(l.s.ready() && l.s.profile() == 3 && l.s.map.valid && l.s.map.pad(0)[9] == 3, "profile 3 is active");
  }
  {
    static Link l;
    l.s.setColor(5, 1, 2, 3);            // set before connecting: sent once ready
    l.s.start(false, l.now);
    l.run(20000);
    const uint8_t* rgb = l.pad.region5 + FAKE_PROFILE * smcpad::PROFILE_SIZE + smcpad::PAD_OFF + 5 * smcpad::PAD_SIZE + 5;
    CHECK(rgb[0] == 1 && rgb[1] == 2 && rgb[2] == 3, "a colour set before READY is sent after it");
  }
  {
    static Link l;
    l.pad.silent = 1;                    // busy: the first identity request is dropped
    l.s.start(false, l.now);
    l.run(20000);
    CHECK(l.s.ready() && l.s.resends == 1 && l.sentLen[0] == l.sentLen[1] && memcmp(l.sent[0], l.sent[1], l.sentLen[0]) == 0,
          "a missing reply -> the same request again after the timeout");
  }
  {
    static Link l;
    l.pad.silent = 100;                  // gone
    l.s.start(false, l.now);
    l.run(5000, false);
    CHECK(l.s.failed() && l.sentCount == Session::TRIES, "three missing replies -> failed (reconnect)");
  }
  {
    Session s;                           // what the controls of the factory profile send
    memcpy(s.map.rec, DEFAULT_PROFILE, smcpad::PROFILE_SIZE);
    s.map.valid = true;
    PadEvent e = ev(s, {0x99, 0x04 + 20, 0x64});
    CHECK(e.kind == PadEvent::PAD && e.index == 20 && e.bank() == 1 && e.pad() == 4 && e.value == 0x64,
          "note on channel 10 -> pad record 20 (bank 1, pad 4), velocity");
    e = ev(s, {0x89, 0x04 + 20, 0x00});
    CHECK(e.kind == PadEvent::PAD && e.index == 20 && e.value == 0, "note off -> released");
    e = ev(s, {0xF0, 0x7F, 0x7F, 0x06, 0x02, 0xF7});
    CHECK(e.kind == PadEvent::BUTTON && e.index == 1 && e.value == 1, "MMC Play -> button 1");
    e = ev(s, {0xB0, 30 + 2, 64});
    CHECK(e.kind == PadEvent::ENCODER && e.index == 2 && e.value == 64 && e.delta == 0, "CC 32 -> encoder 2");
    e = ev(s, {0xB0, 30 + 2, 61});
    CHECK(e.kind == PadEvent::ENCODER && e.delta == -3, "absolute encoder: delta from the previous value");
    e = ev(s, {0xB5, 30, 1});
    CHECK(e.kind == PadEvent::MIDI && e.len == 3 && e.midi[0] == 0xB5, "unknown message -> MIDI");

    uint8_t* b0 = s.map.rec + smcpad::button_addr(0, 0);      // user-made records
    const uint8_t momentary[] = {2, 0, 25, 0, 127};
    memcpy(b0, momentary, sizeof momentary);
    uint8_t* b4 = s.map.rec + smcpad::button_addr(0, 4);
    const uint8_t note[] = {5, 9, 40, 0, 127};
    memcpy(b4, note, sizeof note);
    uint8_t* e7 = s.map.rec + smcpad::encoder_addr(0, 7);
    const uint8_t relative[] = {3, 0, 0, 50, 0x7F, 0x01};
    memcpy(e7, relative, sizeof relative);
    CHECK(ev(s, {0xB0, 25, 127}).kind == PadEvent::BUTTON && ev(s, {0xB0, 25, 127}).value == 1 &&
              ev(s, {0xB0, 25, 0}).value == 0, "momentary CC button: press and release");
    e = ev(s, {0x90, 40, 0x7F});
    CHECK(e.kind == PadEvent::BUTTON && e.index == 4 && e.value == 1, "note button sends on channel 1");
    CHECK(ev(s, {0xB0, 50, 0x01}).delta == 1 && ev(s, {0xB0, 50, 0x7F}).delta == -1, "relative encoder: +1 / -1");

    s.map.valid = false;
    CHECK(ev(s, {0x99, 0x04, 0x7F}).kind == PadEvent::MIDI, "no profile yet -> MIDI");
  }

  printf(fails ? "%d FAILED\n" : "all passed\n", fails);
  return fails ? 1 : 0;
}
