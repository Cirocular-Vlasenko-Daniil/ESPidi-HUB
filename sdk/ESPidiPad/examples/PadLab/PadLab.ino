// PAD LAB — ESPidiPad on the bench: pair an SMC-PAD Pro, watch its events, light its pads, and
// get the numbers the kit's OPEN_QUESTIONS.md asks for (Serial Monitor, 115200).
//
//   PLAY          no pad: pair (put the pad in Bluetooth pairing mode first)
//                 ready:  rainbow on all 8 banks
//   PAGE          all pads dark
//   PAGE 2 s      switch full / quick sync (bench question 1), restarts
//   TAP 2 s       forget the pad
//   ENC           next screen: status / events
//   ENC 1.5 s     back to the HUB
//   pad hit       lights that pad, colour by velocity
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ESPidiHub.h>
#include <ESPidiPad.h>
#include <Preferences.h>
#include <Wire.h>

ESPIDI_FIRMWARE("padlab", "PAD LAB", "0.1.0", "ESPidi HUB");

namespace pin = espidi::pin;
using espidi::PadEvent;

Adafruit_SSD1306 display(espidi::OLED_WIDTH, espidi::OLED_HEIGHT, &Wire, -1);
espidi::Pad pad;
bool fullSync = true;
uint8_t screen = 0;
char lines[3][22];            // the last events
uint32_t readyAt = 0, startedAt = 0;

struct Button {
  uint8_t pin;
  bool down = false;
  uint32_t since = 0;
  bool longDone = false;
};
Button bPlay{pin::PLAY}, bPage{pin::PAGE}, bTap{pin::TAP}, bEnc{pin::ENC_SW};

// 0 = nothing, 1 = short press (on release), 2 = held for `longMs`
uint8_t poll(Button& b, uint32_t longMs) {
  bool now = digitalRead(b.pin) == LOW;
  uint8_t r = 0;
  if (now && !b.down) { b.since = millis(); b.longDone = false; }
  if (now && !b.longDone && millis() - b.since >= longMs) { b.longDone = true; r = 2; }
  if (!now && b.down && !b.longDone && millis() - b.since > 30) r = 1;
  b.down = now;
  return r;
}

void hue(uint8_t h, uint8_t& r, uint8_t& g, uint8_t& b) {   // 0-255 around the colour wheel
  uint8_t s = h % 85 * 3;
  if (h < 85) { r = 255 - s; g = s; b = 0; }
  else if (h < 170) { r = 0; g = 255 - s; b = s; }
  else { r = s; g = 0; b = 255 - s; }
}

void remember(const char* fmt, ...) {
  memmove(lines[0], lines[1], sizeof(lines[0]) * 2);
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(lines[2], sizeof(lines[2]), fmt, ap);
  va_end(ap);
}

void hex(const char* label, const uint8_t* d, size_t n) {
  Serial.print(label);
  for (size_t i = 0; i < n; i++) Serial.printf(" %02X", d[i]);
  Serial.println();
}

// What the kit's bench wants to know, once the pad is ready.
void report() {
  espidi::PadStats s = pad.stats();
  Serial.println("---- PAD LAB report (send this with the results) ----");
  Serial.printf("identity     %s\n", pad.identity().c_str());
  Serial.printf("address      %s, type %s\n", pad.address().c_str(), s.addressType ? "random" : "public");
  Serial.printf("security     encrypted %d, bonded %d\n", s.encrypted, s.bonded);
  Serial.printf("MTU          %u\n", s.mtu);
  Serial.printf("interval     %u us\n", s.intervalUs);
  Serial.printf("sync         %s, ready %lu ms after connect, %lu requests, %lu resends\n", fullSync ? "full" : "quick",
                (unsigned long)(readyAt - startedAt), (unsigned long)s.requests, (unsigned long)s.resends);
  Serial.printf("profile      %u\n", pad.profile());
  uint8_t rec[26];
  char label[16];
  for (uint8_t b = 0; b < 5; b++) {
    snprintf(label, sizeof(label), "button %u  ", b);
    if (pad.buttonRecord(b, rec)) hex(label, rec, 23);
  }
  for (uint8_t e = 0; e < 16; e++) {
    snprintf(label, sizeof(label), "encoder %2u", e);
    if (pad.encoderRecord(e, rec)) hex(label, rec, 6);
  }
  for (uint8_t i = 0; i < 16; i++) {
    snprintf(label, sizeof(label), "pad %3u   ", i);
    if (pad.padRecord(i, rec)) hex(label, rec, 26);
  }
  Serial.println("-----------------------------------------------------");
}

void draw() {
  static const char* STATE[] = {"OFF", "NO PAD", "PAIRING", "CONNECTING", "SYNC", "READY"};
  espidi::Pad::State st = pad.state();
  display.clearDisplay();
  display.setCursor(0, 0);
  if (st == espidi::Pad::SYNCING) display.printf("PAD LAB  SYNC %u%%", pad.progress());
  else display.printf("PAD LAB  %s", STATE[st]);
  if (screen == 0) {
    espidi::PadStats s = pad.stats();
    display.setCursor(0, 8);
    if (st == espidi::Pad::NO_PAD) display.print("PLAY = PAIR");
    else if (st == espidi::Pad::PAIRING) display.print("PAD IN PAIRING MODE");
    else display.printf("%.11s P%u %s", pad.identity().c_str(), pad.profile(), fullSync ? "FULL" : "QUICK");
    display.setCursor(0, 16);
    if (st >= espidi::Pad::SYNCING) display.printf("MTU %u RTT %lums", s.mtu, (unsigned long)s.lastRoundTripMs);
    else display.print(pad.address());
    display.setCursor(0, 24);
    display.print(st == espidi::Pad::READY ? "PLAY=RAINBOW PAGE=OFF" : "TAP 2S = FORGET");
  } else {
    for (uint8_t i = 0; i < 3; i++) {
      display.setCursor(0, 8 + i * 8);
      display.print(lines[i]);
    }
  }
  display.display();
}

void setup() {
  for (uint8_t p : {pin::PLAY, pin::TAP, pin::PAGE, pin::ENC_SW, pin::ENC_A, pin::ENC_B}) pinMode(p, INPUT_PULLUP);
  Serial.begin(115200);
  Wire.begin(pin::OLED_SDA, pin::OLED_SCL);
  display.begin(SSD1306_SWITCHCAPVCC, espidi::OLED_ADDR);
  display.setTextColor(SSD1306_WHITE);

  Preferences prefs;                      // this firmware's own settings: namespace = its id
  prefs.begin("padlab", true);
  fullSync = prefs.getBool("full", true);
  prefs.end();

  espidi::PadOptions opt;
  opt.fullSync = fullSync;
  pad.debug(&Serial);
  pad.begin(opt);
  Serial.printf("PAD LAB 0.1.0, %s sync\n", fullSync ? "full" : "quick");
}

void loop() {
  PadEvent e;
  while (pad.read(e)) {
    uint8_t r, g, b;
    switch (e.kind) {
      case PadEvent::PAD:
        remember("PAD %u/%u V%u", e.bank(), e.pad(), e.value);
        if (e.value) {
          hue((uint8_t)(e.value * 2), r, g, b);
          pad.setColor(e.index, r, g, b);
        }
        break;
      case PadEvent::ENCODER: remember("ENC %u %u %+d", e.index, e.value, e.delta); break;
      case PadEvent::BUTTON: remember("BTN %u %s", e.index, e.value ? "ON" : "OFF"); break;
      case PadEvent::MIDI: {
        char m[22] = "MIDI";
        for (uint8_t i = 0; i < e.len && i < 5; i++) snprintf(m + strlen(m), sizeof(m) - strlen(m), " %02X", e.midi[i]);
        remember("%s", m);
        break;
      }
      case PadEvent::CONNECTED: startedAt = millis(); remember("CONNECTED"); break;
      case PadEvent::READY: readyAt = millis(); remember("READY"); report(); break;
      case PadEvent::DISCONNECTED: remember("DISCONNECTED"); break;
      case PadEvent::PAIRED: remember("PAIRED"); break;
      case PadEvent::PAIR_FAILED: remember("NO PAD FOUND"); break;
      default: break;
    }
    if (e.kind <= PadEvent::MIDI && e.kind != PadEvent::NONE) {
      Serial.printf("%-22s midi", lines[2]);
      for (uint8_t i = 0; i < e.len; i++) Serial.printf(" %02X", e.midi[i]);
      Serial.println();
    }
  }

  uint8_t play = poll(bPlay, 100000), page = poll(bPage, 2000), tap = poll(bTap, 2000), enc = poll(bEnc, 1500);
  if (play == 1) {
    if (!pad.remembered() || pad.state() == espidi::Pad::NO_PAD) pad.pair();
    else if (pad.ready()) {
      static uint8_t shift = 0;
      shift += 37;
      for (uint8_t bank = 0; bank < 8; bank++)
        for (uint8_t k = 0; k < 16; k++) {
          uint8_t r, g, b;
          hue((uint8_t)(k * 16 + shift), r, g, b);
          pad.setColor(espidi::Pad::padIndex(bank, k), r, g, b);
        }
    }
  }
  if (page == 1)
    for (uint8_t bank = 0; bank < 8; bank++) pad.fillBank(bank, 0, 0, 0);
  if (page == 2) {
    Preferences prefs;
    prefs.begin("padlab", false);
    prefs.putBool("full", !fullSync);
    prefs.end();
    ESP.restart();
  }
  if (tap == 2) pad.forget();
  if (enc == 1) screen ^= 1;
  if (enc == 2) espidi::rebootToHub();

  static uint32_t drawn = 0;
  if (millis() - drawn > 50) {
    drawn = millis();
    draw();
  }
}
