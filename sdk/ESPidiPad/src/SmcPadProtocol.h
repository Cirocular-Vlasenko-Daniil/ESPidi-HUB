// From the pi32v2_re project: Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8. See NOTICE.md.
//
// SmcPadProtocol.h: the SMC-PAD Pro config protocol (PROTOCOL.md section 3) and its BLE-MIDI stream (section 2),
// header-only C++11, no STL, no exceptions, no heap, no BLE stack: build packets into a caller's buffer, reassemble
// replies from notifications, split BLE-MIDI notifications. The BLE side (NimBLE central: connect, subscribe to
// AE42 and the MIDI characteristic, write AE41 without response) is the caller's. Checked against the Python
// reference by test/test_protocol.cpp with vectors generated from it (ref/make_vectors.py).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace smcpad {

// ---- GATT (discover by UUID; the handles are firmware SMC-PAD_004's)
static const char* const SVC_CONFIG = "0000ae40-0000-1000-8000-00805f9b34fb";
static const char* const CHR_CONFIG_WRITE = "0000ae41-0000-1000-8000-00805f9b34fb";   // write without response
static const char* const CHR_CONFIG_NOTIFY = "0000ae42-0000-1000-8000-00805f9b34fb";  // notify
static const char* const SVC_MIDI = "03b80e5a-ede8-4b33-a751-6ce34ec4c700";
static const char* const CHR_MIDI = "7772e5db-3868-4112-a1a9-f2669d106bf3";           // read, write w/o rsp, notify

// ---- sizes and layout
static const size_t MAX_PACKET = 0x416;            // 1,046: the pad drops anything longer
static const size_t MAX_DATA = MAX_PACKET - 7;     // 1,039
static const uint32_t REGION4_SIZE = 12;
static const uint32_t REGION5_SIZE = 0x6E98;       // 28,312 = 8 profiles x 3,539
static const uint32_t PROFILE_SIZE = 0xDD3;
static const uint32_t BUTTON_OFF = 0x000, BUTTON_SIZE = 23;
static const uint32_t ENCODER_OFF = 0x073, ENCODER_SIZE = 6;
static const uint32_t PAD_OFF = 0x0D3, PAD_SIZE = 26, PAD_RGB = 5;
static const uint32_t READ_CHUNK = 1009;           // MidiSuite's read size

enum Cmd : uint8_t { OK = 0x00, IDENTITY = 0x11, CONST = 0x12, MIDI = 0x15, OP0 = 0x21, WRITE = 0x22, READ = 0x23,
                     OP3 = 0x24 };

enum Status : int {
  BUILT = 0,          // >= 0: the packet length
  E_BUFFER = -1,      // the output buffer is too small
  E_TOO_LONG = -2,    // over MAX_DATA
  E_REFUSED = -3,     // forbidden by the driver rules (PROTOCOL.md section 5)
  E_RANGE = -4,       // outside the region (the pad does not check: we do)
};

inline uint8_t checksum(const uint8_t* d, size_t n) {
  uint32_t s = 0;
  for (size_t i = 0; i < n; i++) s += d[i];
  return (uint8_t)(~s & 0xFF);
}

// 00 59 cmd len24 data checksum -> out; returns the length or a negative Status
inline int packet(uint8_t cmd, const uint8_t* data, size_t n, uint8_t* out, size_t cap) {
  if (n > MAX_DATA) return E_TOO_LONG;
  if (cap < n + 7) return E_BUFFER;
  out[0] = 0x00; out[1] = 0x59; out[2] = cmd;
  out[3] = (uint8_t)n; out[4] = (uint8_t)(n >> 8); out[5] = (uint8_t)(n >> 16);
  if (n) memcpy(out + 6, data, n);
  out[6 + n] = checksum(out + 6, n);
  return (int)(n + 7);
}

inline void put_hdr(uint8_t* h, uint8_t region, uint32_t addr, uint32_t len) {
  h[0] = region;
  h[1] = (uint8_t)addr; h[2] = (uint8_t)(addr >> 8); h[3] = (uint8_t)(addr >> 16); h[4] = (uint8_t)(addr >> 24);
  h[5] = (uint8_t)len; h[6] = (uint8_t)(len >> 8); h[7] = (uint8_t)(len >> 16);
}

// The driver rules as code: region 0 never; region 3 never written; only regions 3, 4, 5 exist; save0 only through
// save_profiles(); region 5 at 0x80000000 never; every access inside its region.
inline int guard(uint8_t region, uint32_t addr, uint32_t len, bool write) {
  if (region == 0) return E_REFUSED;
  if (region == 3 && write) return E_REFUSED;
  if (region != 3 && region != 4 && region != 5) return E_REFUSED;
  if (write && addr == 0 && len == 0) return E_REFUSED;
  if (region == 5 && (addr & 0x80000000u)) return E_REFUSED;
  uint32_t size = region == 3 ? 0xA0 : region == 4 ? REGION4_SIZE : REGION5_SIZE;
  if ((uint64_t)addr + len > size) return E_RANGE;
  return 0;
}

inline int identity(uint8_t* out, size_t cap) { return packet(IDENTITY, nullptr, 0, out, cap); }

inline int read(uint8_t region, uint32_t addr, uint32_t len, uint8_t* out, size_t cap) {
  int g = guard(region, addr, len, false);
  if (g) return g;
  if (len > MAX_DATA - 8) return E_TOO_LONG;
  uint8_t h[8];
  put_hdr(h, region, addr, len);
  return packet(READ, h, 8, out, cap);
}

inline int write(uint8_t region, uint32_t addr, const uint8_t* data, uint32_t len, uint8_t* out, size_t cap) {
  int g = guard(region, addr, len, true);
  if (g) return g;
  if ((size_t)len + 8 > MAX_DATA) return E_TOO_LONG;
  if (cap < (size_t)len + 15) return E_BUFFER;
  out[0] = 0x00; out[1] = 0x59; out[2] = WRITE;
  uint32_t n = len + 8;
  out[3] = (uint8_t)n; out[4] = (uint8_t)(n >> 8); out[5] = (uint8_t)(n >> 16);
  put_hdr(out + 6, region, addr, len);
  if (len) memcpy(out + 14, data, len);
  out[6 + n] = checksum(out + 6, n);
  return (int)(n + 7);
}

// Region 5 save0: erases 7 flash sectors and writes 28 KiB. Only for an explicit user action (flash wear).
inline int save_profiles(bool user_consented, uint8_t* out, size_t cap) {
  if (!user_consented) return E_REFUSED;
  uint8_t h[8];
  put_hdr(h, 5, 0, 0);
  return packet(WRITE, h, 8, out, cap);
}

// Command 0x15: MIDI bytes into the pad's MIDI queue (its destination is not traced yet)
inline int midi_to_pad(const uint8_t* msg, size_t n, uint8_t* out, size_t cap) { return packet(MIDI, msg, n, out, cap); }

inline uint32_t button_addr(uint8_t profile, uint8_t b) { return profile * PROFILE_SIZE + BUTTON_OFF + b * BUTTON_SIZE; }
inline uint32_t encoder_addr(uint8_t profile, uint8_t e) { return profile * PROFILE_SIZE + ENCODER_OFF + e * ENCODER_SIZE; }
inline uint32_t pad_addr(uint8_t profile, uint8_t i) { return profile * PROFILE_SIZE + PAD_OFF + i * PAD_SIZE; }

// One pad's colour, RAM only (PROTOCOL.md 4.2)
inline int color_write(uint8_t profile, uint8_t pad, uint8_t r, uint8_t g, uint8_t b, uint8_t* out, size_t cap) {
  if (profile > 7 || pad > 127) return E_RANGE;
  const uint8_t rgb[3] = {r, g, b};
  return write(5, pad_addr(profile, pad) + PAD_RGB, rgb, 3, out, cap);
}

// ---- replies: a parsed packet and the reassembler for AE42 notifications

struct Packet {
  uint8_t cmd;
  const uint8_t* data;   // points into the Reassembler's buffer: valid until the next feed()
  size_t len;
  bool ok() const { return cmd == OK && len >= 1 && data[0] == 0; }
};

// Feed each AE42 notification; call next() until it returns false to take the complete packets out.
class Reassembler {
 public:
  void reset() { n_ = 0; taken_ = 0; }
  // returns false when the piece did not fit (then the buffer was reset)
  bool feed(const uint8_t* p, size_t len) {
    compact();
    if (len > sizeof(buf_) - n_) { reset(); if (len > sizeof(buf_)) return false; }
    memcpy(buf_ + n_, p, len);
    n_ += len;
    return true;
  }
  bool next(Packet& out) {
    compact();
    for (;;) {
      size_t i = 0;
      while (i + 1 < n_ && !(buf_[i] == 0x00 && buf_[i + 1] == 0x59)) i++;
      if (i + 1 >= n_) {                                     // no header: keep a trailing 00
        if (n_ && buf_[n_ - 1] == 0x00) { buf_[0] = 0x00; n_ = 1; } else n_ = 0;
        return false;
      }
      if (i) { memmove(buf_, buf_ + i, n_ - i); n_ -= i; }
      if (n_ < 6) return false;
      size_t total = (size_t)buf_[3] | ((size_t)buf_[4] << 8) | ((size_t)buf_[5] << 16);
      total += 7;
      if (total > MAX_PACKET) { memmove(buf_, buf_ + 2, n_ - 2); n_ -= 2; continue; }
      if (n_ < total) return false;
      if (buf_[total - 1] == checksum(buf_ + 6, total - 7)) {
        out.cmd = buf_[2]; out.data = buf_ + 6; out.len = total - 7;
        taken_ = total;                                      // removed on the next call
        return true;
      }
      memmove(buf_, buf_ + total, n_ - total);              // bad checksum: drop it
      n_ -= total;
    }
  }

 private:
  void compact() {
    if (taken_) { memmove(buf_, buf_ + taken_, n_ - taken_); n_ -= taken_; taken_ = 0; }
  }
  uint8_t buf_[2 * MAX_PACKET];
  size_t n_ = 0, taken_ = 0;
};

// The data of a 0x23 reply, after checking that it answers read(region, addr, len): the 8-byte header is echoed
inline const uint8_t* read_reply(const Packet& p, uint8_t region, uint32_t addr, uint32_t len) {
  uint8_t h[8];
  put_hdr(h, region, addr, len);
  if (p.cmd != READ || p.len != 8 + (size_t)len || memcmp(p.data, h, 8) != 0) return nullptr;
  return p.data + 8;
}

// ---- BLE-MIDI notifications from the pad: calls on_msg(timestamp13, msg, n) for each message
template <typename F>
inline void ble_midi_messages(const uint8_t* p, size_t n, F on_msg) {
  if (n < 3 || !(p[0] & 0x80)) return;
  uint16_t hi = (uint16_t)(p[0] & 0x3F) << 7, ts = 0;
  uint8_t status = 0, msg[3];
  size_t i = 1;
  while (i < n) {
    if (p[i] & 0x80) {                                      // a timestamp byte
      ts = hi | (p[i] & 0x7F);
      if (++i >= n) break;
    }
    if (p[i] & 0x80) status = p[i++];
    if (!status) break;
    if (status == 0xF0) {                                   // SysEx within one notification (MMC buttons)
      uint8_t sx[64]; size_t k = 0;
      sx[k++] = 0xF0;
      while (i < n) {
        if (p[i] < 0x80) { if (k < sizeof(sx) - 1) sx[k++] = p[i]; i++; }
        else if (p[i] == 0xF7 || (i + 1 < n && p[i + 1] == 0xF7)) { sx[k++] = 0xF7; i += p[i] == 0xF7 ? 1 : 2; break; }
        else i++;
      }
      on_msg(ts, sx, k);
      status = 0;
      continue;
    }
    if (status >= 0xF8) { msg[0] = status; on_msg(ts, msg, 1); continue; }
    uint8_t hiN = status & 0xF0;
    size_t len = (hiN == 0xC0 || hiN == 0xD0) ? 1 : 2;
    if (i + len > n) break;
    msg[0] = status; memcpy(msg + 1, p + i, len);
    on_msg(ts, msg, len + 1);
    i += len;
  }
}

}  // namespace smcpad
