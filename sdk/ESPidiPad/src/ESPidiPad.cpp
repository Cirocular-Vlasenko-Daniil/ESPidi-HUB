// ESPidiPad, the BLE half: scanning and pairing, reconnecting to the remembered pad, GATT
// (config service AE40 and BLE-MIDI), and a background task that runs padcore::Session — one
// config request in flight, replies reassembled from AE42 notifications. loop() only reads
// events and records LED wishes; both sides share the session under a mutex.
#include "ESPidiPad.h"

#include <BLEClient.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLESecurity.h>
#include <Preferences.h>
#include <esp_timer.h>
#include <stdarg.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/stream_buffer.h>
#include <freertos/task.h>

#if !defined(CONFIG_NIMBLE_ENABLED)
#error "ESPidiPad needs Arduino-ESP32 3.3+ with its BLE library on NimBLE (the ESP32-C3 default)"
#endif
#include <host/ble_gap.h>
#include <host/ble_store.h>

namespace espidi {
namespace {

const char* const NVS_NS = "espidi_pad";        // shared by every firmware on the board
const uint32_t CONNECT_TIMEOUT_US = 4000 * 1000;
const uint32_t RETRY_MIN_MS = 500, RETRY_MAX_MS = 5000;
const uint8_t MAX_REJECTED = 8;

// What BLE callbacks hand to loop(): a MIDI message (mapped in read()) or a state change.
struct Item {
  uint8_t kind;   // PadEvent::Kind
  uint8_t len;
  uint8_t b[24];  // a button's raw message is at most 17 bytes
};

class Driver : public BLEClientCallbacks {
 public:
  PadOptions opt;
  QueueHandle_t events = nullptr;
  StreamBufferHandle_t replies = nullptr;
  SemaphoreHandle_t lock = nullptr;
  TaskHandle_t task = nullptr;
  esp_timer_handle_t cancelTimer = nullptr;
  BLEClient* client = nullptr;
  BLERemoteCharacteristic *cfgWrite = nullptr, *cfgNotify = nullptr, *midi = nullptr;
  padcore::Session session;
  smcpad::Reassembler rx;
  Print* log = nullptr;

  volatile Pad::State state = Pad::OFF;
  volatile bool wantPair = false, wantForget = false, linkLost = false, connecting = false;
  bool known = false, encrypted = false, bonded = false;
  ble_addr_t peer = {};
  char ident[28] = {0};
  uint16_t mtu = 23, intervalUs = 0;
  uint32_t dropped = 0;
  ble_addr_t rejected[MAX_REJECTED] = {};
  uint8_t rejectedCount = 0;
  uint8_t tx[smcpad::MAX_PACKET];
  uint8_t chunk[600];

  void onConnect(BLEClient*) override {}
  void onDisconnect(BLEClient*) override {
    linkLost = true;
    if (task) xTaskNotifyGive(task);
  }

  void say(const char* fmt, ...) {
    if (!log) return;
    char line[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    log->print("[pad] ");
    log->println(line);
  }

  void push(PadEvent::Kind k) {
    Item it = {};
    it.kind = k;
    if (xQueueSend(events, &it, 0) != pdTRUE) dropped++;
  }

  void take() { xSemaphoreTake(lock, portMAX_DELAY); }
  void give() { xSemaphoreGive(lock); }

  // Waits up to ms, or less when pair() / forget() asks for attention.
  void nap(uint32_t ms) { ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(ms)); }

  // ---- the remembered pad (NVS "espidi_pad": addr = 6 bytes as NimBLE keeps them, type, id)
  void load() {
    Preferences p;
    if (!p.begin(NVS_NS, true)) return;
    if (p.getBytesLength("addr") == 6) {
      p.getBytes("addr", peer.val, 6);
      peer.type = p.getUChar("type", BLE_ADDR_PUBLIC);
      p.getString("id", ident, sizeof(ident));
      known = true;
    }
    p.end();
  }
  void save() {
    Preferences p;
    if (!p.begin(NVS_NS, false)) return;
    p.putBytes("addr", peer.val, 6);
    p.putUChar("type", peer.type);
    p.putString("id", ident);
    p.end();
  }

  // ---- task
  static void main(void* arg) { static_cast<Driver*>(arg)->run(); }

  void run() {
    uint32_t retry = RETRY_MIN_MS;
    for (;;) {
      if (wantForget) doForget();
      if (wantPair) {
        doPair();
        continue;
      }
      if (!known) {
        state = Pad::NO_PAD;
        nap(1000);
        continue;
      }
      state = Pad::CONNECTING;
      if (open(peer)) {
        say("connected to the remembered pad");
        online();
        retry = RETRY_MIN_MS;
      } else {
        nap(retry);
        retry = retry * 2 > RETRY_MAX_MS ? RETRY_MAX_MS : retry * 2;
      }
    }
  }

  static void cancelConnect(void*) { ble_gap_conn_cancel(); }

  // Connects and sets up GATT: AE41 (write), AE42 and BLE-MIDI notifications.
  bool open(const ble_addr_t& a) {
    linkLost = false;
    connecting = true;
    esp_timer_start_once(cancelTimer, CONNECT_TIMEOUT_US);   // NimBLE's own connect timeout is 30 s
    bool ok = client->connect(BLEAddress(a), a.type);
    esp_timer_stop(cancelTimer);
    connecting = false;
    if (!ok) return false;
    mtu = client->getMTU();
    struct ble_gap_conn_desc desc;
    encrypted = false;
    if (ble_gap_conn_find(client->getConnId(), &desc) == 0) {
      intervalUs = desc.conn_itvl * 1250;
      encrypted = desc.sec_state.encrypted;
      bonded = desc.sec_state.bonded;
    }
    BLERemoteService* cfg = client->getService(smcpad::SVC_CONFIG);
    BLERemoteService* mid = client->getService(smcpad::SVC_MIDI);
    cfgWrite = cfg ? cfg->getCharacteristic(smcpad::CHR_CONFIG_WRITE) : nullptr;
    cfgNotify = cfg ? cfg->getCharacteristic(smcpad::CHR_CONFIG_NOTIFY) : nullptr;
    midi = mid ? mid->getCharacteristic(smcpad::CHR_MIDI) : nullptr;
    if (!cfgWrite || !cfgNotify || !midi) {
      say("no AE40 config service or BLE-MIDI: not an SMC-PAD");
      client->disconnect();
      return false;
    }
    xStreamBufferReset(replies);
    rx.reset();
    bool sub = cfgNotify->subscribe(true, [this](BLERemoteCharacteristic*, uint8_t* d, size_t n, bool) {
      xStreamBufferSend(replies, d, n, 0);
    });
    sub = sub && midi->subscribe(true, [this](BLERemoteCharacteristic*, uint8_t* d, size_t n, bool) {
      smcpad::ble_midi_messages(d, n, [this](uint16_t, const uint8_t* m, size_t k) {
        Item it = {};
        it.kind = PadEvent::MIDI;
        it.len = (uint8_t)(k < sizeof(it.b) ? k : sizeof(it.b));
        memcpy(it.b, m, it.len);
        if (xQueueSend(events, &it, 0) != pdTRUE) dropped++;
      });
    });
    if (!sub) {
      say("subscribing to AE42 / BLE-MIDI failed");
      client->disconnect();
      return false;
    }
    say("address type %u, MTU %u, interval %u us, encrypted %d, bonded %d", a.type, mtu, intervalUs, encrypted, bonded);
    return true;
  }

  // A logical packet as GATT writes without response of at most MTU-3 bytes; the pad joins them.
  bool send(const uint8_t* p, size_t n) {
    size_t piece = mtu > 23 ? mtu - 3 : 20;
    for (size_t off = 0; off < n; off += piece) {
      size_t k = n - off < piece ? n - off : piece;
      for (int tries = 0; !cfgWrite->writeValue(const_cast<uint8_t*>(p + off), k, false); tries++) {
        if (tries > 20 || !client->isConnected()) return false;
        vTaskDelay(pdMS_TO_TICKS(5));     // out of buffers: let the controller send
      }
    }
    return true;
  }

  // Runs the session until the link drops, fails, or pair() / forget() interrupts.
  void online() {
    take();
    session.start(opt.fullSync, millis());
    give();
    state = Pad::SYNCING;
    push(PadEvent::CONNECTED);
    uint32_t t0 = millis();
    bool wasReady = false;
    while (client->isConnected() && !linkLost && !wantPair && !wantForget) {
      take();
      int n = session.poll(millis(), tx, sizeof(tx));
      bool failed = session.failed(), ready = session.ready();
      give();
      if (failed) {
        say("no reply after %u tries: reconnecting", padcore::Session::TRIES);
        break;
      }
      if (n > 0) send(tx, n);           // a lost write shows as a missing reply and is resent
      size_t got = xStreamBufferReceive(replies, chunk, sizeof(chunk), pdMS_TO_TICKS(n > 0 ? 2 : 10));
      if (got) {
        rx.feed(chunk, got);
        smcpad::Packet p;
        take();
        while (rx.next(p)) session.reply(p, millis());
        ready = session.ready();
        if (session.identity()[0] && strcmp(ident, session.identity()) != 0) strncpy(ident, session.identity(), sizeof(ident) - 1);
        give();
      }
      if (ready && !wasReady) {
        say("ready in %lu ms: %s, profile %u", (unsigned long)(millis() - t0), session.identity(), session.profile());
        state = Pad::READY;
        push(PadEvent::READY);
      } else if (!ready && wasReady) {
        state = Pad::SYNCING;            // the active profile changed on the pad: reading it
      }
      wasReady = ready;
    }
    if (client->isConnected()) client->disconnect();
    take();
    session.stop();
    give();
    state = known ? Pad::CONNECTING : Pad::NO_PAD;
    push(PadEvent::DISCONNECTED);
    say("disconnected");
  }

  // One request and its reply outside the session (identity check while pairing).
  bool identify(char* out, size_t cap) {
    uint8_t pkt[8];
    int n = smcpad::identity(pkt, sizeof(pkt));
    for (int tries = 0; tries < 3; tries++) {
      send(pkt, n);
      uint32_t t0 = millis();
      while (millis() - t0 < padcore::Session::TIMEOUT_MS) {
        size_t got = xStreamBufferReceive(replies, chunk, sizeof(chunk), pdMS_TO_TICKS(20));
        if (!got) continue;
        rx.feed(chunk, got);
        smcpad::Packet p;
        while (rx.next(p)) {
          if (p.cmd != smcpad::IDENTITY) continue;
          size_t k = p.len < cap - 1 ? p.len : cap - 1;
          memcpy(out, p.data, k);
          out[k] = 0;
          return true;
        }
      }
    }
    return false;
  }

  bool isRejected(const ble_addr_t& a) const {
    for (uint8_t i = 0; i < rejectedCount; i++)
      if (memcmp(rejected[i].val, a.val, 6) == 0) return true;
    return false;
  }

  void doPair() {
    wantPair = false;
    state = Pad::PAIRING;
    rejectedCount = 0;
    if (client->isConnected()) client->disconnect();
    BLEScan* scan = BLEDevice::getScan();
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(99);
    say("looking for a pad in pairing mode");
    uint32_t t0 = millis();
    while (millis() - t0 < opt.pairTimeoutMs && !wantForget) {
      BLEScanResults* res = scan->start(1, false);
      ble_addr_t best = {};
      int bestRssi = -1000;
      for (int i = 0; res && i < res->getCount(); i++) {
        BLEAdvertisedDevice d = res->getDevice(i);
        String name = d.haveName() ? d.getName() : String();
        name.toUpperCase();
        bool midiAdv = d.haveServiceUUID() && d.isAdvertisingService(BLEUUID(smcpad::SVC_MIDI));
        if (name.indexOf("SMC") < 0 && !midiAdv) continue;
        ble_addr_t a;
        memcpy(a.val, d.getAddress().getNative(), 6);
        a.type = d.getAddressType();
        if (isRejected(a) || d.getRSSI() <= bestRssi) continue;
        best = a;
        bestRssi = d.getRSSI();
        say("candidate %s \"%s\" rssi %d", d.getAddress().toString().c_str(), d.getName().c_str(), d.getRSSI());
      }
      scan->clearResults();
      if (bestRssi == -1000) continue;
      char id[28] = {0};
      // A fresh pairing: an old bond (e.g. after FORGET PAD in the HUB) would be ignored by the
      // client, which doesn't answer the stack's repeat-pairing question.
      ble_store_util_delete_peer(&best);
      bool ok = open(best) && identify(id, sizeof(id));
      if (!ok || strncmp(id, "SMC-PAD", 7) != 0) {
        say("not an SMC-PAD (identity \"%s\")", id);
        if (client->isConnected()) client->disconnect();
        if (rejectedCount < MAX_REJECTED) rejected[rejectedCount++] = best;
        continue;
      }
      if (opt.bond) {
        bool sec = client->secureConnection();
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(client->getConnId(), &desc) == 0) {
          encrypted = desc.sec_state.encrypted;
          bonded = desc.sec_state.bonded;
        }
        say("just-works pairing: %s (encrypted %d, bonded %d)", sec ? "ok" : "failed", encrypted, bonded);
      }
      peer = best;
      take();
      strncpy(ident, id, sizeof(ident) - 1);
      give();
      known = true;
      save();
      say("paired with %s", id);
      push(PadEvent::PAIRED);
      if (client->isConnected()) online();
      return;
    }
    say("no pad found");
    state = known ? Pad::CONNECTING : Pad::NO_PAD;
    push(PadEvent::PAIR_FAILED);
  }

  void doForget() {
    wantForget = false;
    if (client->isConnected()) client->disconnect();
    if (known) ble_gap_unpair(&peer);      // the bond, if any
    Preferences p;
    if (p.begin(NVS_NS, false)) {
      p.clear();
      p.end();
    }
    known = false;
    take();
    ident[0] = 0;
    give();
    state = Pad::NO_PAD;
    say("pad forgotten");
  }
};

Driver* g = nullptr;
Print* debugOut = nullptr;   // debug() may come before begin()

}  // namespace

bool Pad::begin(const PadOptions& options) {
  if (g) return true;
  g = new Driver();
  g->opt = options;
  g->log = debugOut;
  g->events = xQueueCreate(64, sizeof(Item));
  g->replies = xStreamBufferCreate(4096, 1);
  g->lock = xSemaphoreCreateMutex();
  esp_timer_create_args_t t = {};
  t.callback = &Driver::cancelConnect;
  t.name = "pad_connect";
  if (!g->events || !g->replies || !g->lock || esp_timer_create(&t, &g->cancelTimer) != ESP_OK) return false;
  g->load();
  if (!BLEDevice::init("ESPidi")) return false;
  BLEDevice::setMTU(517);
  if (options.bond) {
    BLESecurity::setAuthenticationMode(true, false, true);   // bonding, no MITM (no display on the pad)
    BLESecurity::setCapability(ESP_IO_CAP_NONE);
  }
  g->client = BLEDevice::createClient();
  g->client->setClientCallbacks(g);
  g->state = g->known ? CONNECTING : NO_PAD;
  return xTaskCreate(&Driver::main, "espidi_pad", 8192, g, 2, &g->task) == pdPASS;
}

bool Pad::read(PadEvent& e) {
  Item it;
  if (!g || xQueueReceive(g->events, &it, 0) != pdTRUE) return false;
  if (it.kind == PadEvent::MIDI) {
    g->take();
    g->session.map.map(it.b, it.len, e);
    g->give();
  } else {
    e = PadEvent();
    e.kind = (PadEvent::Kind)it.kind;
  }
  return true;
}

void Pad::pair() {
  if (!g) return;
  g->wantPair = true;
  if (g->connecting) ble_gap_conn_cancel();
  xTaskNotifyGive(g->task);
}

void Pad::forget() {
  if (!g) return;
  g->wantForget = true;
  if (g->connecting) ble_gap_conn_cancel();
  xTaskNotifyGive(g->task);
}

Pad::State Pad::state() const { return g ? g->state : OFF; }
bool Pad::remembered() const { return g && g->known; }

uint8_t Pad::progress() const {
  if (!g) return 0;
  g->take();
  uint8_t p = g->session.progress();
  g->give();
  return p;
}

String Pad::identity() const {
  if (!g) return String();
  g->take();
  String s(g->ident);
  g->give();
  return s;
}

String Pad::address() const {
  if (!g || !g->known) return String();
  char s[18];
  const uint8_t* v = g->peer.val;   // NimBLE keeps the address least significant byte first
  snprintf(s, sizeof(s), "%02x:%02x:%02x:%02x:%02x:%02x", v[5], v[4], v[3], v[2], v[1], v[0]);
  return String(s);
}

uint8_t Pad::profile() const {
  if (!g) return 0;
  g->take();
  uint8_t p = g->session.profile();
  g->give();
  return p;
}

PadStats Pad::stats() const {
  PadStats s = {};
  if (!g) return s;
  g->take();
  s.requests = g->session.requests;
  s.resends = g->session.resends;
  s.writeErrors = g->session.writeErrors;
  s.lastRoundTripMs = g->session.lastRtt;
  s.maxRoundTripMs = g->session.maxRtt;
  g->give();
  s.droppedEvents = g->dropped;
  s.mtu = g->mtu;
  s.intervalUs = g->intervalUs;
  s.addressType = g->peer.type;
  s.encrypted = g->encrypted;
  s.bonded = g->bonded;
  return s;
}

bool Pad::setColor(uint8_t pad, uint8_t r, uint8_t gr, uint8_t b) {
  if (!g) return false;
  g->take();
  bool ok = g->session.setColor(pad, r, gr, b);
  g->give();
  return ok;
}

void Pad::fillBank(uint8_t bank, uint8_t r, uint8_t gr, uint8_t b) {
  if (!g || bank >= padcore::BANKS) return;
  g->take();
  for (uint8_t k = 0; k < padcore::BANK_PADS; k++) g->session.setColor(padIndex(bank, k), r, gr, b);
  g->give();
}

bool Pad::getColor(uint8_t pad, uint8_t rgb[3]) const {
  if (!g) return false;
  g->take();
  bool ok = g->session.getColor(pad, rgb);
  g->give();
  return ok;
}

namespace {
bool copyRecord(const uint8_t* (padcore::Mapper::*at)(uint8_t) const, uint8_t i, uint8_t limit, uint8_t* out, size_t n) {
  if (!g || i >= limit) return false;
  g->take();
  bool ok = g->session.map.valid;
  if (ok) memcpy(out, (g->session.map.*at)(i), n);
  g->give();
  return ok;
}
}  // namespace

bool Pad::padRecord(uint8_t pad, uint8_t out[26]) const {
  return copyRecord(&padcore::Mapper::pad, pad, padcore::PADS, out, smcpad::PAD_SIZE);
}
bool Pad::encoderRecord(uint8_t encoder, uint8_t out[6]) const {
  return copyRecord(&padcore::Mapper::encoder, encoder, padcore::ENCODERS, out, smcpad::ENCODER_SIZE);
}
bool Pad::buttonRecord(uint8_t button, uint8_t out[23]) const {
  return copyRecord(&padcore::Mapper::button, button, padcore::BUTTONS, out, smcpad::BUTTON_SIZE);
}

void Pad::debug(Print* out) {
  debugOut = out;
  if (g) g->log = out;
}

}  // namespace espidi
