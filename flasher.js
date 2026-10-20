// Web Serial + esptool-js: connect to an ESPidi, read what is installed, write a plan.
import { ESPLoader, Transport } from "./vendor/esptool-js/bundle.js";
import * as E from "./espidi.js";

export const webSerialSupported = () => typeof navigator !== "undefined" && "serial" in navigator;

export class DeviceError extends Error {
  constructor(code, detail = "") {
    super(code);
    this.code = code;
    this.detail = detail;
  }
}

export class Device {
  constructor(log) {
    this.log = log;
    this.loader = null;
    this.transport = null;
    this.chip = "";
  }

  async connect() {
    const port = await navigator.serial.requestPort();
    this.transport = new Transport(port, false);
    const terminal = {
      clean() {},
      writeLine: (s) => this.log(s),
      write: (s) => this.log(s, true),
    };
    this.loader = new ESPLoader({ transport: this.transport, baudrate: 921600, terminal, debugLogging: false });
    this.chip = await this.loader.main();
    if (!/ESP32-C3/i.test(this.chip)) {
      await this.disconnect();
      throw new DeviceError("not_c3", this.chip);
    }
    return this.chip;
  }

  // { hub: false } or { hub: true, layout, meta } — same reading as tools/espidi_hub.py read_state().
  async readState() {
    const pt = await this.loader.readFlash(E.PT_OFFSET, E.PT_MAX);
    let layout = null;
    try {
      layout = E.layoutFromTable(E.parseTable(pt));
    } catch {
      layout = null;
    }
    if (!layout) return { hub: false };
    const meta = E.unpackMeta(await this.loader.readFlash(E.META_OFFSET, E.META_SIZE));
    meta.forEach((m, i) => {
      if (!m.imageSize && layout.slot[i] === E.PLACEHOLDER) layout.slot[i] = 0;  // placeholder of an empty slot
    });
    return { hub: true, layout, meta };
  }

  // Images first, then the partition table and slot info — as two separate writes.
  async run(plan, progress) {
    if (plan.erase) {
      this.log("erase flash");
      await this.loader.eraseFlash();
    }
    const last = plan.files.filter((f) => f.address === E.PT_OFFSET || f.address === E.META_OFFSET);
    const first = plan.files.filter((f) => !last.includes(f));
    for (const group of [first, last]) {
      if (!group.length) continue;
      await this.loader.writeFlash({
        fileArray: group.map((f) => ({ data: f.data, address: f.address })),
        flashMode: "keep",
        flashFreq: "keep",
        flashSize: "keep",
        eraseAll: false,
        compress: true,
        reportProgress: (i, written, total) => progress(group[i], written, total),
        calculateMD5Hash: E.md5hex,
      });
    }
  }

  async reset() {
    try {
      await this.loader.after("hard_reset");
    } catch {
      // the port may already be gone after the reset
    }
  }

  async disconnect() {
    try {
      await this.transport?.disconnect();
    } catch {
      // already closed
    }
    this.loader = null;
    this.transport = null;
  }
}
