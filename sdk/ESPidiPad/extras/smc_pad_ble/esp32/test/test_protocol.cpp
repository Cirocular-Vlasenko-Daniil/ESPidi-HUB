// From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md.
// Host test of SmcPadProtocol.h against the Python reference's vectors (ref/make_vectors.py -> vectors.h).
//   c++ -std=c++11 -I.. test_protocol.cpp -o test_protocol && ./test_protocol
#include <stdio.h>
#include <string.h>

#include "SmcPadProtocol.h"
#include "vectors.h"

using namespace smcpad;

static int fails = 0;

static void expect(const char* name, const uint8_t* got, int n) {
  for (const Vec& v : VECTORS) {
    if (strcmp(v.name, name) != 0) continue;
    if (n < 0 || (size_t)n != v.n || memcmp(got, v.bytes, v.n) != 0) {
      printf("FAIL %s (got %d bytes)\n", name, n);
      fails++;
    } else {
      printf("ok   %s\n", name);
    }
    return;
  }
  printf("FAIL %s: no such vector\n", name);
  fails++;
}

static void expect_refused(const char* what, int r) {
  if (r == E_REFUSED || r == E_RANGE) printf("ok   refused %s\n", what);
  else { printf("FAIL %s was built (%d)\n", what, r); fails++; }
}

int main() {
  uint8_t b[MAX_PACKET];
  expect("identity", b, identity(b, sizeof b));
  expect("read_region4", b, read(4, 0, 12, b, sizeof b));
  expect("read_region5_first", b, read(5, 0, 1009, b, sizeof b));
  expect("read_region5_last", b, read(5, 0x6E5C, 60, b, sizeof b));
  expect("color_p0_pad3_red", b, color_write(0, 3, 255, 0, 0, b, sizeof b));
  expect("color_p0_pad32_black", b, color_write(0, 32, 0, 0, 0, b, sizeof b));
  expect("color_p7_pad127_white", b, color_write(7, 127, 255, 255, 255, b, sizeof b));
  const uint8_t one = 1;
  expect("write_region4_byte10", b, write(4, 10, &one, 1, b, sizeof b));
  expect("save_profiles", b, save_profiles(true, b, sizeof b));
  const uint8_t note[3] = {0x90, 0x24, 0x7F};
  expect("midi_to_pad_note", b, midi_to_pad(note, 3, b, sizeof b));

  const uint8_t z = 0, rgb[3] = {1, 2, 3};
  expect_refused("read region 0", read(0, 0, 4, b, sizeof b));
  expect_refused("write region 3", write(3, 1, &z, 1, b, sizeof b));
  expect_refused("write past region 5", write(5, 0x6E96, rgb, 3, b, sizeof b));
  expect_refused("region 5 at 0x80000000", read(5, 0x80000000u, 1, b, sizeof b));
  expect_refused("save0 without consent", save_profiles(false, b, sizeof b));
  expect_refused("save0 through write()", write(5, 0, nullptr, 0, b, sizeof b));

  // reassembly of a reply cut into 5-byte notifications, with junk before it and an OK after
  const uint8_t rd[] = {0x00, 0x59, 0x23, 0x14, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x00, 0x00, 0x78,
                        0x00, 0x32, 0x04, 0x00, 0x00, 0x01, 0x01, 0x03, 0x01, 0x01, 0x00, 0x3A};
  const uint8_t okp[] = {0x00, 0x59, 0x00, 0x01, 0x00, 0x00, 0x00, 0xFF};
  uint8_t stream[64];
  size_t sn = 0;
  stream[sn++] = 0x13; stream[sn++] = 0x37;
  memcpy(stream + sn, rd, sizeof rd); sn += sizeof rd;
  memcpy(stream + sn, okp, sizeof okp); sn += sizeof okp;
  Reassembler r;
  int got_read = 0, got_ok = 0;
  for (size_t k = 0; k < sn; k += 5) {
    r.feed(stream + k, sn - k < 5 ? sn - k : 5);
    Packet p;
    while (r.next(p)) {
      if (p.cmd == READ) {
        const uint8_t* d = read_reply(p, 4, 0, 12);
        got_read += d && d[0] == 0x78 && d[10] == 0x01;
      }
      got_ok += p.ok();
    }
  }
  if (got_read == 1 && got_ok == 1) printf("ok   reassembler\n");
  else { printf("FAIL reassembler (read %d ok %d)\n", got_read, got_ok); fails++; }

  // BLE-MIDI: the captured note, then a running-status burst
  const uint8_t n1[] = {0x80, 0x80, 0x99, 0x16, 0x7F};
  const uint8_t n2[] = {0x81, 0x82, 0xB0, 0x1E, 0x10, 0x83, 0x1E, 0x11, 0x1E, 0x12};
  int count = 0, good = 0;
  ble_midi_messages(n1, sizeof n1, [&](uint16_t ts, const uint8_t* m, size_t n) {
    count++; good += ts == 0 && n == 3 && m[0] == 0x99 && m[1] == 0x16 && m[2] == 0x7F; });
  ble_midi_messages(n2, sizeof n2, [&](uint16_t, const uint8_t* m, size_t n) {
    count++; good += n == 3 && m[0] == 0xB0 && m[1] == 0x1E; });
  if (count == 4 && good == 4) printf("ok   ble-midi\n");
  else { printf("FAIL ble-midi (%d messages, %d good)\n", count, good); fails++; }

  printf(fails ? "%d FAILED\n" : "all passed\n", fails);
  return fails ? 1 : 0;
}
