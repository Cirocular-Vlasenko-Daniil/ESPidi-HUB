// ESPidi HUB core for the web: layout, partition table, metadata sector, image checks.
// A port of tools/espidi_hub.py — keep both in sync (web/test.html compares them byte for byte).

export const FLASH_SIZE = 0x400000;
export const BOOTLOADER_OFFSET = 0x0;
export const PT_OFFSET = 0x8000, PT_MAX = 0xC00;
export const NVS_OFFSET = 0x9000, NVS_SIZE = 0x4000;
export const META_OFFSET = 0xD000, META_SIZE = 0x1000;
export const OTADATA_OFFSET = 0xE000, OTADATA_SIZE = 0x2000;
export const HUB_OFFSET = 0x10000, HUB_SIZE = 0x40000;
export const APP_START = HUB_OFFSET + HUB_SIZE;   // 0x50000
export const APP_END = 0x300000;
export const FS_OFFSET = APP_END, FS_SIZE = FLASH_SIZE - APP_END;
export const APP_ALIGN = 0x10000;
export const PLACEHOLDER = 0x10000;
export const AREA = APP_END - APP_START;
export const MAX_IMAGE = AREA - PLACEHOLDER;
export const SLOTS = 2;

const TYPE_APP = 0x00, TYPE_DATA = 0x01, TYPE_ESPIDI = 0x40;
const SUB_FACTORY = 0x00, SUB_OTA0 = 0x10, SUB_OTA1 = 0x11, SUB_OTA = 0x00, SUB_NVS = 0x02, SUB_SPIFFS = 0x82;

export const alignUp = (n, a = APP_ALIGN) => Math.ceil(n / a) * a;

// ---------------------------------------------------------------------------
// Layout: slot 1 grows up from APP_START, slot 2 grows down to APP_END.
// ---------------------------------------------------------------------------
export class Layout {
  constructor(slot = [0, 0]) {
    this.slot = [...slot];          // bytes given to each slot, 0 = empty
  }
  alloc(i) { return this.slot[i] || PLACEHOLDER; }
  offset(i) { return i === 0 ? APP_START : APP_END - this.alloc(1); }
  maxSize(i) { return AREA - this.alloc(1 - i); }
  free() { return AREA - this.slot[0] - this.slot[1]; }
  fits() { return this.alloc(0) + this.alloc(1) <= AREA; }
  partitions() {
    return [
      { name: "nvs", type: TYPE_DATA, subtype: SUB_NVS, offset: NVS_OFFSET, size: NVS_SIZE },
      { name: "espidi", type: TYPE_ESPIDI, subtype: 0x00, offset: META_OFFSET, size: META_SIZE },
      { name: "otadata", type: TYPE_DATA, subtype: SUB_OTA, offset: OTADATA_OFFSET, size: OTADATA_SIZE },
      { name: "hub", type: TYPE_APP, subtype: SUB_FACTORY, offset: HUB_OFFSET, size: HUB_SIZE },
      { name: "slot1", type: TYPE_APP, subtype: SUB_OTA0, offset: this.offset(0), size: this.alloc(0) },
      { name: "slot2", type: TYPE_APP, subtype: SUB_OTA1, offset: this.offset(1), size: this.alloc(1) },
      { name: "spiffs", type: TYPE_DATA, subtype: SUB_SPIFFS, offset: FS_OFFSET, size: FS_SIZE },
    ];
  }
}

// ---------------------------------------------------------------------------
// MD5 (partition table checksum, esptool verify) and CRC-32 (metadata)
// ---------------------------------------------------------------------------
const MD5_K = Int32Array.from({ length: 64 }, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) | 0);
const MD5_S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];

export function md5(bytes) {
  const len = bytes.length;
  const blocks = ((len + 8) >>> 6) + 1;
  const w = new Int32Array(blocks * 16);
  for (let i = 0; i < len; i++) w[i >> 2] |= bytes[i] << ((i % 4) * 8);
  w[len >> 2] |= 0x80 << ((len % 4) * 8);
  w[blocks * 16 - 2] = (len * 8) | 0;
  w[blocks * 16 - 1] = Math.floor(len / 0x20000000);
  let a0 = 0x67452301, b0 = 0xefcdab89 | 0, c0 = 0x98badcfe | 0, d0 = 0x10325476;
  for (let blk = 0; blk < w.length; blk += 16) {
    let a = a0, b = b0, c = c0, d = d0;
    for (let i = 0; i < 64; i++) {
      let f, g;
      if (i < 16) { f = (b & c) | (~b & d); g = i; }
      else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
      else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
      else { f = c ^ (b | ~d); g = (7 * i) % 16; }
      const x = (a + f + MD5_K[i] + w[blk + g]) | 0;
      const s = MD5_S[(i >> 4) * 4 + (i % 4)];
      a = d; d = c; c = b;
      b = (b + ((x << s) | (x >>> (32 - s)))) | 0;
    }
    a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
  }
  const out = new Uint8Array(16);
  const dv = new DataView(out.buffer);
  [a0, b0, c0, d0].forEach((v, i) => dv.setInt32(i * 4, v, true));
  return out;
}

export const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const md5hex = (bytes) => toHex(md5(bytes));

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

// ---------------------------------------------------------------------------
// Partition table (same bytes as ESP-IDF gen_esp32part.py)
// ---------------------------------------------------------------------------
function putStr(buf, at, str, n) {
  buf.fill(0, at, at + n);
  const bytes = new TextEncoder().encode(str);
  buf.set(bytes.subarray(0, n - 1), at);
}

function getStr(buf, at, n) {
  const slice = buf.subarray(at, at + n);
  const end = slice.indexOf(0);
  return new TextDecoder("ascii").decode(end >= 0 ? slice.subarray(0, end) : slice);
}

export function buildTable(parts) {
  const out = new Uint8Array(PT_MAX).fill(0xFF);
  const dv = new DataView(out.buffer);
  parts.forEach((p, k) => {
    const o = k * 32;
    out[o] = 0xAA; out[o + 1] = 0x50; out[o + 2] = p.type; out[o + 3] = p.subtype;
    dv.setUint32(o + 4, p.offset, true);
    dv.setUint32(o + 8, p.size, true);
    out.fill(0, o + 12, o + 28);
    out.set(new TextEncoder().encode(p.name).subarray(0, 16), o + 12);
    dv.setUint32(o + 28, p.flags || 0, true);
  });
  const n = parts.length * 32;
  out[n] = 0xEB; out[n + 1] = 0xEB;           // bytes n+2 .. n+15 stay 0xFF
  out.set(md5(out.subarray(0, n)), n + 16);
  return out;
}

export function parseTable(data) {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const parts = [];
  for (let o = 0; o + 32 <= data.length; o += 32) {
    if (data[o] === 0xAA && data[o + 1] === 0x50) {
      parts.push({
        type: data[o + 2], subtype: data[o + 3],
        offset: dv.getUint32(o + 4, true), size: dv.getUint32(o + 8, true),
        name: getStr(data, o + 12, 16), flags: dv.getUint32(o + 28, true),
      });
    } else {
      if (data[o] === 0xEB && data[o + 1] === 0xEB && toHex(md5(data.subarray(0, o))) !== toHex(data.subarray(o + 16, o + 32))) {
        throw new Error("partition table MD5 mismatch");
      }
      break;
    }
  }
  return parts;
}

// The layout of a device, or null if it has no ESPidi HUB.
export function layoutFromTable(parts) {
  const by = Object.fromEntries(parts.map((p) => [p.name, p]));
  const s1 = by.slot1, s2 = by.slot2;
  if (!(s1 && s2 && by.hub && s1.offset === APP_START && s2.offset + s2.size === APP_END)) return null;
  return new Layout([s1.size, s2.size]);
}

// ---------------------------------------------------------------------------
// "espidi" metadata sector (C side: hub/main/slots.c)
// ---------------------------------------------------------------------------
const META_MAGIC = "ESPIDIHB";
const META_HEAD = 12, META_SLOT = 88, META_BODY = META_HEAD + SLOTS * META_SLOT;   // 188

export const emptyMeta = () => ({ id: "", name: "", version: "", author: "", imageSize: 0, imageCrc32: 0 });

export function packMeta(slots) {
  const out = new Uint8Array(META_SIZE).fill(0xFF);
  const dv = new DataView(out.buffer);
  out.set(new TextEncoder().encode(META_MAGIC), 0);
  dv.setUint16(8, 1, true);
  dv.setUint16(10, 0, true);
  slots.forEach((m, i) => {
    const o = META_HEAD + i * META_SLOT;
    putStr(out, o, m.id, 16);
    putStr(out, o + 16, m.name, 24);
    putStr(out, o + 40, m.version, 16);
    putStr(out, o + 56, m.author, 24);
    dv.setUint32(o + 80, m.imageSize, true);
    dv.setUint32(o + 84, m.imageCrc32 >>> 0, true);
  });
  dv.setUint32(META_BODY, crc32(out.subarray(0, META_BODY)), true);
  return out;
}

export function unpackMeta(data) {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const ok = getStr(data, 0, 8) === META_MAGIC && dv.getUint16(8, true) === 1 &&
    crc32(data.subarray(0, META_BODY)) === dv.getUint32(META_BODY, true);
  return Array.from({ length: SLOTS }, (_, i) => {
    if (!ok) return emptyMeta();
    const o = META_HEAD + i * META_SLOT;
    return {
      id: getStr(data, o, 16), name: getStr(data, o + 16, 24), version: getStr(data, o + 40, 16),
      author: getStr(data, o + 56, 24), imageSize: dv.getUint32(o + 80, true), imageCrc32: dv.getUint32(o + 84, true),
    };
  });
}

// ---------------------------------------------------------------------------
// Firmware images
// ---------------------------------------------------------------------------
const CHIP_ID_ESP32C3 = 5;
const APP_DESC_MAGIC = 0xABCD5432;
const DESC_MAGIC = new TextEncoder().encode("ESPIDI-FW-1\0");

function findBytes(hay, needle) {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

// Problems are codes; the UI translates them (see i18n.js).
export function inspectImage(data) {
  const info = { size: data.length, chipId: -1, project: "", appVersion: "", idfVersion: "", desc: null, problems: [] };
  if (data.length < 0x20 + 144 || data[0] !== 0xE9) {
    info.problems.push({ code: "not_image" });
    return info;
  }
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  info.chipId = dv.getUint16(12, true);
  if (info.chipId !== CHIP_ID_ESP32C3) info.problems.push({ code: "wrong_chip", chipId: info.chipId });
  if (dv.getUint32(0x20, true) === APP_DESC_MAGIC) {
    info.appVersion = getStr(data, 0x30, 32);
    info.project = getStr(data, 0x50, 32);
    info.idfVersion = getStr(data, 0x90, 32);
  } else {
    info.problems.push({ code: "no_app_desc" });
  }
  const pos = findBytes(data, DESC_MAGIC);
  if (pos >= 0 && pos + 92 <= data.length) {
    info.desc = {
      id: getStr(data, pos + 12, 16), name: getStr(data, pos + 28, 24),
      version: getStr(data, pos + 52, 16), author: getStr(data, pos + 68, 24),
    };
  }
  if (alignUp(data.length) > MAX_IMAGE) info.problems.push({ code: "too_big", size: data.length });
  return info;
}

// ---------------------------------------------------------------------------
// What to write. A slot source is one of:
//   { kind: "empty" }
//   { kind: "keep" }                                (device update only)
//   { kind: "image", data: Uint8Array, meta: { id, name, version, author } }
// ---------------------------------------------------------------------------
export function slotMetaFor(src) {
  return { id: src.meta.id || "", name: src.meta.name || "", version: src.meta.version || "",
    author: src.meta.author || "", imageSize: src.data.length, imageCrc32: crc32(src.data) };
}

export function composeLayout(sources, device = null) {
  return new Layout(sources.map((s, i) => {
    if (s.kind === "image") return alignUp(s.data.length);
    if (s.kind === "keep" && device) return device.layout.slot[i];
    return 0;
  }));
}

// Fresh install: everything from the bootloader up to the end of the firmware area.
// Flashed at 0x0 it also clears NVS (settings); the file partition is not part of it.
export function buildImage({ bootloader, hub, sources }) {
  const layout = composeLayout(sources);
  const out = new Uint8Array(APP_END).fill(0xFF);
  out.set(bootloader, BOOTLOADER_OFFSET);
  out.set(buildTable(layout.partitions()), PT_OFFSET);
  out.set(packMeta(sources.map((s) => (s.kind === "image" ? slotMetaFor(s) : emptyMeta()))), META_OFFSET);
  out.set(hub, HUB_OFFSET);
  sources.forEach((s, i) => { if (s.kind === "image") out.set(s.data, layout.offset(i)); });
  return { image: out, layout };
}

// Device writes. Order matters: images first, then empty-slot placeholders, then the
// partition table and metadata — an interrupted write leaves the old table in charge and a
// half-written slot fails validation, so the HUB starts.
export function planWrites({ sources, device, full, bootloader, hub }) {
  const layout = composeLayout(sources, full ? null : device);
  const meta = sources.map((s, i) => {
    if (s.kind === "image") return slotMetaFor(s);
    if (s.kind === "keep" && device && !full) return device.meta[i];
    return emptyMeta();
  });
  const files = [];
  if (full) files.push({ address: BOOTLOADER_OFFSET, data: bootloader, label: "bootloader" },
    { address: HUB_OFFSET, data: hub, label: "HUB" });
  sources.forEach((s, i) => {
    if (s.kind === "image") files.push({ address: layout.offset(i), data: s.data, label: `slot ${i + 1}` });
  });
  if (!full) {
    sources.forEach((s, i) => {
      if (s.kind === "empty" && device.layout.slot[i]) {
        files.push({ address: layout.offset(i), data: new Uint8Array(PLACEHOLDER).fill(0xFF), label: `slot ${i + 1} (empty)` });
      }
    });
  }
  files.push({ address: PT_OFFSET, data: buildTable(layout.partitions()), label: "partition table" },
    { address: META_OFFSET, data: packMeta(meta), label: "slot info" });
  return { layout, meta, files, erase: full };
}
